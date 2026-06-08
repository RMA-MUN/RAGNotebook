import asyncio
import base64
import os
import re

from langchain_core.messages import HumanMessage

from app.core.background_init import init_manager
from app.core.logger_handler import logger

# 批量视觉识别模板：要求模型按固定格式输出每个页面的描述，
# 格式为 "--- Page N ---" + 描述内容，便于后续用正则解析。
_BATCH_PROMPT_TEMPLATE = """请逐页描述以下多张文档页面图片。

每张图片对应一个页面，请严格按照以下格式输出每个页面的描述：

--- Page [页码] ---
[该页的详细描述，包括文字内容、图片/图表/表格、布局结构等]

确保每个页面的描述前都有 "--- Page N ---" 标记（N为页码），不同页面的描述之间用空行隔开。"""

_BATCH_TEXT_REF_TEMPLATE = """以下是一些页面已有的文本内容（仅供参考，图片中的文字更优先）:
{refs}"""


class VisionService:
    """
    多模态视觉服务——将图片发送给视觉模型进行描述（支持单页和批量）。

    为什么需要这个服务？
    传统 PDF 解析只能提取文本，无法获取图片、图表、流程图中的信息。
    本服务通过调用视觉大模型（如 Qwen-VL），对 PDF 页面截图进行"看图说话"，
    将视觉信息转化为文本描述，补充到 Document 内容中，提升 RAG 检索质量。
    """

    def __init__(self, model=None):
        self._model = model

    def _get_model(self):
        if self._model is not None:
            return self._model
        model = init_manager.vision_model
        if model is None:
            raise RuntimeError("视觉模型尚未初始化完成，请稍后重试")
        self._model = model
        return model

    def _is_ollama(self) -> bool:
        """检测当前使用的模型是否为 Ollama 本地部署模型"""
        return 'ChatOllama' in type(self._get_model()).__name__

    def _encode_image(self, image_path: str) -> tuple[str, str]:
        """
        读取图片文件，返回 base64 编码和 MIME 类型。
        LangChain 的 HumanMessage 支持 data URL 格式的图片输入：
        data:image/png;base64,xxxxx
        Ollama 和阿里云百炼都支持这种格式。
        """
        ext = os.path.splitext(image_path)[1].lower()
        mime_map = {
            '.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
            '.tiff': 'image/tiff', '.tif': 'image/tiff', '.bmp': 'image/bmp',
            '.gif': 'image/gif', '.webp': 'image/webp',
        }
        mime = mime_map.get(ext, 'image/png')
        with open(image_path, "rb") as f:
            img_b64 = base64.b64encode(f.read()).decode("utf-8")
        return img_b64, mime

    def _build_prompt(self, existing_text: str) -> str:
        """
        构造单页识别的 prompt。
        将已有的纯文本提取结果作为上下文提供给视觉模型，辅助模型理解页面内容。
        但最终以视觉模型的输出为准（提示词中写明"图片中的文字更优先"），
        因为视觉模型能看到版面布局，不会受文本提取顺序的影响。
        """
        text_part = (
            f"页面已有文本（仅供参考）:\n{existing_text[:800]}"
            if existing_text.strip()
            else "该页没有提取到文本。"
        )
        return (
            "请详细描述这张文档页面图片中的内容：\n\n"
            "1. 提取页面中的所有文字信息，保持原文表述\n"
            "2. 描述页面中的图片、图表、流程图、表格等视觉元素的内容和作用\n"
            "3. 如果有表格，提取表格的结构和数据\n"
            "4. 说明页面整体的布局结构\n\n"
            f"{text_part}"
        )

    def _build_batch_prompt(self, pages_info: list[dict]) -> str:
        """构造批量识别的 prompt，包含每页已有的文本作为参考"""
        text_refs = []
        for info in pages_info:
            txt = info.get("text", "").strip()
            if txt:
                text_refs.append(
                    f"--- Page {info['page']} 已有文本 ---\n{txt[:800]}"
                )

        if text_refs:
            ref_block = _BATCH_TEXT_REF_TEMPLATE.format(
                refs="\n\n".join(text_refs)
            )
            return f"{_BATCH_PROMPT_TEMPLATE}\n\n{ref_block}"
        return _BATCH_PROMPT_TEMPLATE

    def _parse_batch_response(
        self, response_text: str, expected_pages: list[int]
    ) -> dict[int, str]:
        """
        解析批量视觉模型返回的文本，提取每个页面的描述。
        使用正则匹配 "--- Page N ---" 格式，如果没有匹配到则尝试用启发式方法分割。
        """
        result = {}

        # 优先匹配严格格式：--- Page 1 --- 描述内容
        pattern = r"--- Page (\d+) ---\s*(.*?)(?=--- Page \d+ ---|\Z)"
        matches = re.findall(pattern, response_text.strip(), re.DOTALL)

        if matches:
            for page_num_str, description in matches:
                result[int(page_num_str)] = description.strip()

        # 如果所有页面都解析到了，直接返回
        if result and all(p in result for p in expected_pages):
            return result

        # 容错处理：如果模型没有按格式输出，尝试按行数平均分割（粗略 fallback）
        if not result:
            lines = response_text.strip().split('\n')
            if len(expected_pages) == 1:
                result[expected_pages[0]] = response_text.strip()
            else:
                per_page = max(1, len(lines) // len(expected_pages))
                for i, pn in enumerate(expected_pages):
                    start = i * per_page
                    end = start + per_page if i < len(expected_pages) - 1 else len(lines)
                    result[pn] = '\n'.join(lines[start:end]).strip()
        else:
            # 部分页面解析到了，缺失的页面用已解析的第一个页面内容填充
            for pn in expected_pages:
                if pn not in result:
                    first_key = next(iter(result))
                    result[pn] = result[first_key]

        return result

    def _build_message_from_b64(
        self, img_b64: str, mime: str, existing_text: str
    ) -> HumanMessage:
        """构造 LangChain HumanMessage（单图），包含文字 prompt 和图片 data URL"""
        prompt = self._build_prompt(existing_text)
        return HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{img_b64}"}}
        ])

    def _build_batch_message_from_b64(
        self,
        images_info: list[tuple[str, str, str]],
        page_numbers: list[int],
    ) -> HumanMessage:
        """
        构造 LangChain HumanMessage（多图批量），包含：
        - 一个描述所有页面的统一 prompt
        - 多张图片的 data URL
        这样做可以减少 API 调用次数，让视觉模型一次性处理多页。
        """
        prompt = self._build_batch_prompt([
            {"page": pn, "text": txt}
            for pn, (_, _, txt) in zip(page_numbers, images_info)
        ])
        content = [{"type": "text", "text": prompt}]
        for img_b64, mime, _ in images_info:
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:{mime};base64,{img_b64}"}
            })
        return HumanMessage(content=content)

    def _dashscope_describe(self, img_b64: str, mime: str, existing_text: str) -> str:
        import dashscope

        api_key = getattr(self._get_model(), 'api_key', None) or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
        model_name = self._get_model().model_name

        messages = [{
            "role": "user",
            "content": [
                {"image": f"data:{mime};base64,{img_b64}"},
                {"text": self._build_prompt(existing_text)}
            ]
        }]

        response = dashscope.MultiModalConversation.call(
            model=model_name,
            messages=messages,
            api_key=api_key,
        )

        if response is None:
            logger.error("【视觉服务】DashScope 返回 None，可能是网络错误或请求超时")
            return ""

        if response.status_code != 200:
            logger.error(
                f"【视觉服务】DashScope 返回错误，status_code: {response.status_code}, "
                f"code: {getattr(response, 'code', '')}, message: {getattr(response, 'message', '')}"
            )
            return ""

        if response.output is None:
            logger.error("【视觉服务】DashScope 返回 output 为 None，可能是服务暂时不可用")
            return ""

        choices = response.output.choices
        if not choices:
            logger.error("【视觉服务】DashScope 返回空 choices")
            return ""

        content_list = choices[0].message.content
        if isinstance(content_list, list) and len(content_list) > 0:
            return content_list[0].get("text", "")
        return str(content_list) if content_list else ""

    def _dashscope_describe_batch(
        self,
        images_info: list[tuple[str, str, str]],
        page_numbers: list[int],
    ) -> str:
        import dashscope

        api_key = getattr(self._get_model(), 'api_key', None) or os.getenv("ALIYUN_ACCESS_KEY_SECRET")
        model_name = self._get_model().model_name

        prompt = self._build_batch_prompt([
            {"page": pn, "text": txt}
            for pn, (_, _, txt) in zip(page_numbers, images_info)
        ])

        content = [{"text": prompt}]
        for img_b64, mime, _ in images_info:
            content.append({"image": f"data:{mime};base64,{img_b64}"})

        messages = [{"role": "user", "content": content}]

        response = dashscope.MultiModalConversation.call(
            model=model_name,
            messages=messages,
            api_key=api_key,
        )

        if response is None:
            logger.error("【视觉服务·批量】DashScope 返回 None，可能是网络错误或请求超时")
            return ""

        if response.status_code != 200:
            logger.error(
                f"【视觉服务·批量】DashScope 返回错误，status_code: {response.status_code}, "
                f"code: {getattr(response, 'code', '')}, message: {getattr(response, 'message', '')}"
            )
            return ""

        if response.output is None:
            logger.error("【视觉服务·批量】DashScope 返回 output 为 None，可能是服务暂时不可用")
            return ""

        choices = response.output.choices
        if not choices:
            logger.error("【视觉服务·批量】DashScope 返回空 choices")
            return ""

        content_list = choices[0].message.content
        if isinstance(content_list, list) and len(content_list) > 0:
            return content_list[0].get("text", "")
        return str(content_list) if content_list else ""

    async def describe_page(self, image_path: str, existing_text: str = "") -> str:
        """异步单页视觉描述"""
        if not os.path.exists(image_path):
            logger.error(f"【视觉服务】图片文件不存在: {image_path}")
            return ""

        try:
            img_b64, mime = self._encode_image(image_path)

            if self._is_ollama():
                # Ollama：使用 LangChain 的 ChatOllama，支持多模态 HumanMessage
                message = self._build_message_from_b64(img_b64, mime, existing_text)
                response = await self._get_model().ainvoke([message])
                return str(response.content)
            else:
                # 阿里云百炼：DashScope 的 API 不兼容 LangChain 的 HumanMessage 格式，
                # 需要使用 DashScope 原生 SDK 调用（通过 asyncio.to_thread 避免阻塞事件循环）
                return await asyncio.to_thread(
                    self._dashscope_describe, img_b64, mime, existing_text
                )
        except Exception as e:
            logger.error(f"【视觉服务】视觉模型调用失败: {e}")
            return ""

    def describe_page_sync(self, image_path: str, existing_text: str = "") -> str:
        """同步单页视觉描述（用于 ThreadPoolExecutor 环境）"""
        if not os.path.exists(image_path):
            logger.error(f"【视觉服务】图片文件不存在: {image_path}")
            return ""

        try:
            img_b64, mime = self._encode_image(image_path)

            if self._is_ollama():
                message = self._build_message_from_b64(img_b64, mime, existing_text)
                response = self._get_model().invoke([message])
                return str(response.content)
            else:
                return self._dashscope_describe(img_b64, mime, existing_text)
        except Exception as e:
            logger.error(f"【视觉服务·同步】调用失败: {e}")
            return existing_text if existing_text.strip() else ""

    async def describe_pages_batch(
        self,
        image_paths: list[str],
        page_numbers: list[int],
        existing_texts: list[str],
    ) -> dict[int, str]:
        """
        异步批量视觉描述。
        将多张页面图片一次性发送给视觉模型，要求按页分别描述。
        相比逐页调用，批量可以减少 HTTP 请求次数和 token 消耗（共享 prompt 前缀）。
        """
        for path in image_paths:
            if not os.path.exists(path):
                logger.error(f"【视觉服务·批量】图片文件不存在: {path}")
                return {pn: "" for pn in page_numbers}

        try:
            images_info = []
            for path, txt in zip(image_paths, existing_texts):
                img_b64, mime = self._encode_image(path)
                images_info.append((img_b64, mime, txt))

            if self._is_ollama():
                message = self._build_batch_message_from_b64(images_info, page_numbers)
                response = await self._get_model().ainvoke([message])
                raw_text = str(response.content)
            else:
                raw_text = await asyncio.to_thread(
                    self._dashscope_describe_batch, images_info, page_numbers
                )

            result = self._parse_batch_response(raw_text, page_numbers)
            logger.info(
                f"【视觉服务·批量】成功: {len(page_numbers)}页 -> {len(result)}页解析结果 "
                f"(页: {page_numbers})"
            )
            return result

        except Exception as e:
            logger.error(f"【视觉服务·批量】调用失败: {e}")
            return {
                pn: existing_texts[i] if existing_texts[i].strip() else ""
                for i, pn in enumerate(page_numbers)
            }

    def describe_pages_batch_sync(
        self,
        image_paths: list[str],
        page_numbers: list[int],
        existing_texts: list[str],
    ) -> dict[int, str]:
        """同步批量视觉描述（用于 ThreadPoolExecutor 环境）"""
        for path in image_paths:
            if not os.path.exists(path):
                logger.error(f"【视觉服务·批量·同步】图片文件不存在: {path}")
                return {pn: "" for pn in page_numbers}

        try:
            images_info = []
            for path, txt in zip(image_paths, existing_texts):
                img_b64, mime = self._encode_image(path)
                images_info.append((img_b64, mime, txt))

            if self._is_ollama():
                message = self._build_batch_message_from_b64(images_info, page_numbers)
                response = self._get_model().invoke([message])
                raw_text = str(response.content)
            else:
                raw_text = self._dashscope_describe_batch(images_info, page_numbers)

            return self._parse_batch_response(raw_text, page_numbers)

        except Exception as e:
            logger.error(f"【视觉服务·批量·同步】调用失败: {e}")
            return {
                pn: existing_texts[i] if existing_texts[i].strip() else ""
                for i, pn in enumerate(page_numbers)
            }

    def compute_image_hash(self, image_path: str) -> str:
        """
        计算图片的感知哈希（pHash）。
        用于判断两张图片是否视觉相似——同一份 PDF 的不同页可能包含相同的装饰性图片
        （如页眉、背景图），通过去重可以避免重复调用视觉模型，节省成本和延迟。
        """
        try:
            import imagehash
            from PIL import Image
            with Image.open(image_path) as img:
                return str(imagehash.phash(img))
        except ImportError:
            logger.warning(
                "【视觉服务】imagehash 或 Pillow 未安装，无法进行图片去重"
            )
            return ""
        except Exception as e:
            logger.error(f"【视觉服务】计算图片哈希失败: {e}")
            return ""

    @staticmethod
    def hamming_distance(hash1: str, hash2: str) -> int:
        """
        计算两个 pHash 的汉明距离。
        距离越小代表图片越相似，当距离 <= DEDUP_THRESHOLD 时视为重复页面。
        汉明距离为 0 代表完全相同的图片。
        """
        if not hash1 or not hash2:
            return 999
        try:
            import imagehash
            return imagehash.hex_to_hash(hash1) - imagehash.hex_to_hash(hash2)
        except Exception:
            return 999
