import asyncio
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

import fitz
from langchain_core.documents import Document

from app.core.logger_handler import logger
from app.utils.image_extractor import extract_images_from_pdf
from app.utils.path_tool import get_abstract_path
from app.utils.vision_service import VisionService

# 环境变量配置项（可被 .env 覆盖），用于控制多模态 PDF 加载的行为：
# - BATCH_SIZE:    每批次发送给视觉模型的页数（越大越快，但受限于视觉模型的上下文窗口）
# - DEDUP_ENABLED: 是否对视觉相似的页面去重（如 PPT 模板页、重复的页眉页脚）
# - DEDUP_THRESHOLD: 感知哈希的汉明距离阈值，越小去重越严格
# - BATCH_LOW_RES: 使用低分辨率渲染页面图片（节省带宽和视觉模型的计算成本）
_BATCH_SIZE = int(os.getenv("VISION_BATCH_SIZE", "5"))
_DEDUP_ENABLED = os.getenv("VISION_DEDUP_ENABLED", "true").lower() == "true"
_DEDUP_THRESHOLD = int(os.getenv("VISION_DEDUP_THRESHOLD", "10"))
_LOW_RES_BATCH = os.getenv("VISION_BATCH_LOW_RES", "true").lower() == "true"


@dataclass
class _PageVisionData:
    """保存单页PDF的处理中间状态：文本、图片列表、临时渲染文件、视觉模型描述文本、感知哈希值"""
    page_num: int
    text: str
    has_images: bool
    image_paths: list
    temp_path: str
    vision_text: str = ""
    phash: str = ""


def _build_document(
    content: str,
    page_num: int,
    md5: str,
    source: str,
    image_paths: list,
    has_images: bool,
) -> Document:
    """
    构造 LangChain Document 对象。
    关键 metadata 说明：
    - md5:       文档的 MD5 值，用于关联提取的图片目录
    - image_paths: 该页提取的图片文件名列表（相对路径），存入向量库后随检索结果一起返回
    - has_images:  该页是否包含图片（前端可根据此字段决定是否展示图片区域）
    """
    return Document(
        page_content=content,
        metadata={
            "page": page_num,
            "md5": md5,
            "source": source,
            "image_paths": image_paths if image_paths else None,
            "has_images": has_images,
        }
    )


async def pdf_multimodal_loader(file_path: str, md5: str, user_id: str) -> list[Document]:
    """
    多模态 PDF 加载器（异步版）。

    处理流程：
    1. 使用 PyMuPDF 打开 PDF
    2. 逐页提取文本 + 图片（图片保存到磁盘）
    3. 对"包含图片"或"文本过少"的页面，渲染为图片并调用视觉模型获取描述
    4. 将视觉描述追加到文本中，作为该页的 Document 内容

    设计决策：
    - 为什么需要视觉模型？纯文本提取无法获取图表、流程图、表格结构等视觉信息，
      视觉模型可以"看图说话"，补全这些缺失的信息，提升 RAG 检索的召回质量。
    - 为什么要去重？PDF 中经常有重复的装饰性图片（如页眉页脚、背景图），
      对每个页面都调用视觉模型是浪费的。通过感知哈希去重，相似的页面只调用一次。
    - 为什么要分批？视觉模型 API 通常支持批量图片输入，分批可以减少 API 调用次数，
      提高吞吐量。
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(abs_file_path):
        logger.error(f"【多模态PDF加载】文件不存在: {abs_file_path}")
        return []

    vision = VisionService()
    # 第1步：提取PDF中所有嵌入的原始图片，保存到磁盘
    images_map = extract_images_from_pdf(abs_file_path, user_id, md5)

    try:
        doc = fitz.open(abs_file_path)
    except Exception as e:
        logger.error(f"【多模态PDF加载】打开PDF失败: {e}")
        return []

    total_pages = len(doc)
    documents: list[Document] = []
    vision_data: list[_PageVisionData] = []
    temp_files: list[str] = []
    source_name = os.path.basename(abs_file_path)

    # 渲染分辨率：低分模式用 72dpi（Matrix(1,1)），高分模式用 144dpi（Matrix(2,2)）
    # 视觉模型只需要理解页面布局和图表的大致内容，低分辨率足够且速度更快
    matrix = fitz.Matrix(1, 1) if _LOW_RES_BATCH else fitz.Matrix(2, 2)

    for page_num in range(total_pages):
        page = doc[page_num]
        text = page.get_text().strip()
        page_images = images_map.get(page_num, [])
        has_images = len(page_images) > 0

        # 第2步：判断是否需要视觉模型处理——有图片或文本太少（<100字符）时启用
        if has_images or len(text) < 100:
            pix = page.get_pixmap(matrix=matrix)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                    temp_path = f.name
                pix.save(temp_path)
                temp_files.append(temp_path)
            except Exception as e:
                logger.error(f"【多模态PDF加载】渲染第{page_num+1}页失败: {e}")
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
                documents.append(_build_document(
                    text, page_num + 1, md5, source_name,
                    page_images, has_images,
                ))
                continue

            vision_data.append(_PageVisionData(
                page_num=page_num + 1,
                text=text,
                has_images=has_images,
                image_paths=page_images,
                temp_path=temp_path,
            ))
        else:
            # 纯文本页面，无需视觉模型处理，直接作为 Document
            documents.append(_build_document(
                text, page_num + 1, md5, source_name,
                page_images, False,
            ))

    doc.close()

    if not vision_data:
        logger.info(f"【多模态PDF加载】处理完成: {len(documents)} 页（全部纯文本）")
        return documents

    try:
        groups = []
        unique_data_indices = list(range(len(vision_data)))

        # 第3步（可选）：感知哈希去重——对视觉相似的页面只调用一次视觉模型
        # 使用 phash（感知哈希）计算页面图片的指纹，汉明距离小于阈值则视为相同
        if _DEDUP_ENABLED:
            try:
                for vd in vision_data:
                    vd.phash = vision.compute_image_hash(vd.temp_path)

                groups = []
                for i, vd in enumerate(vision_data):
                    matched = False
                    for rep_idx, indices in groups:
                        dist = VisionService.hamming_distance(
                            vision_data[rep_idx].phash, vd.phash
                        )
                        if dist <= _DEDUP_THRESHOLD:
                            indices.append(i)
                            matched = True
                            break
                    if not matched:
                        groups.append((i, [i]))

                unique_data_indices = [g[0] for g in groups]
                dedup_count = len(vision_data) - len(unique_data_indices)
                if dedup_count > 0:
                    logger.info(
                        f"【多模态PDF加载】去重: {len(vision_data)}需视觉处理 -> "
                        f"{len(unique_data_indices)}唯一, 节省{dedup_count}次调用"
                    )
            except Exception as e:
                logger.warning(f"【多模态PDF加载】去重失败(跳过): {e}")
                groups = [(i, [i]) for i in range(len(vision_data))]
        else:
            groups = [(i, [i]) for i in range(len(vision_data))]

        # 第4步：拆分批次
        batches = []
        for i in range(0, len(unique_data_indices), _BATCH_SIZE):
            batch_indices = unique_data_indices[i:i + _BATCH_SIZE]
            batches.append({
                "image_paths": [vision_data[idx].temp_path for idx in batch_indices],
                "page_numbers": [vision_data[idx].page_num for idx in batch_indices],
                "texts": [vision_data[idx].text for idx in batch_indices],
                "data_indices": batch_indices,
            })

        # 第5步：并发调用视觉模型，所有批次并行执行
        if batches:
            tasks = [
                vision.describe_pages_batch(
                    b["image_paths"], b["page_numbers"], b["texts"]
                )
                for b in batches
            ]
            all_results = await asyncio.gather(*tasks)

            for batch, result in zip(batches, all_results):
                for data_idx in batch["data_indices"]:
                    pn = vision_data[data_idx].page_num
                    vision_data[data_idx].vision_text = result.get(pn, "")

        # 第6步（去重模式）：将代表页面的视觉描述复制给同一组内的重复页面
        if _DEDUP_ENABLED:
            for rep_idx, indices in groups:
                rep_text = vision_data[rep_idx].vision_text
                if not rep_text:
                    continue
                for idx in indices:
                    if idx != rep_idx:
                        vision_data[idx].vision_text = rep_text

        # 第7步：将视觉描述合并到文本中，构造最终的 Document
        for vd in vision_data:
            if vd.text and vd.vision_text:
                # 有文本也有视觉描述：合并两者，前缀标明来源
                content = f"{vd.text}\n\n[页面视觉描述]: {vd.vision_text}"
            elif vd.vision_text:
                # 仅有视觉描述（页面可能完全是图片，原始文本为空）
                content = vd.vision_text
            else:
                content = vd.text

            documents.append(_build_document(
                content, vd.page_num, md5, source_name,
                vd.image_paths, vd.has_images,
            ))

    finally:
        # 清理所有临时渲染文件（页面图片渲染的 PNG）
        for tp in temp_files:
            try:
                if os.path.exists(tp):
                    os.unlink(tp)
            except Exception:
                pass

    documents.sort(key=lambda d: d.metadata["page"])
    vision_count = sum(1 for d in documents if d.metadata["has_images"])
    logger.info(
        f"【多模态PDF加载】完成: {len(documents)}页 "
        f"(含图{vision_count}页, 批次={len(batches)}, "
        f"去重={_DEDUP_ENABLED}, 低分={_LOW_RES_BATCH})"
    )
    return documents


def pdf_multimodal_loader_sync(file_path: str, md5: str, user_id: str) -> list[Document]:
    """
    多模态 PDF 加载器（同步版）。
    逻辑与异步版完全一致，区别仅在于视觉模型调用使用 ThreadPoolExecutor 而非 asyncio.gather。
    用于 SSE 上传流程中的 _sync_slice_file 多线程环境（那里用的是 ThreadPoolExecutor）。
    """
    abs_file_path = get_abstract_path(file_path) if not os.path.isabs(file_path) else file_path
    if not os.path.exists(abs_file_path):
        logger.error(f"【多模态PDF加载·同步】文件不存在: {abs_file_path}")
        return []

    vision = VisionService()
    images_map = extract_images_from_pdf(abs_file_path, user_id, md5)

    try:
        doc = fitz.open(abs_file_path)
    except Exception as e:
        logger.error(f"【多模态PDF加载·同步】打开PDF失败: {e}")
        return []

    total_pages = len(doc)
    documents: list[Document] = []
    vision_data: list[_PageVisionData] = []
    temp_files: list[str] = []
    source_name = os.path.basename(abs_file_path)

    matrix = fitz.Matrix(1, 1) if _LOW_RES_BATCH else fitz.Matrix(2, 2)

    for page_num in range(total_pages):
        page = doc[page_num]
        text = page.get_text().strip()
        page_images = images_map.get(page_num, [])
        has_images = len(page_images) > 0

        if has_images or len(text) < 100:
            pix = page.get_pixmap(matrix=matrix)
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as f:
                    temp_path = f.name
                pix.save(temp_path)
                temp_files.append(temp_path)
            except Exception as e:
                logger.error(f"【多模态PDF加载·同步】渲染第{page_num+1}页失败: {e}")
                if temp_path and os.path.exists(temp_path):
                    os.unlink(temp_path)
                documents.append(_build_document(
                    text, page_num + 1, md5, source_name,
                    page_images, has_images,
                ))
                continue

            vision_data.append(_PageVisionData(
                page_num=page_num + 1,
                text=text,
                has_images=has_images,
                image_paths=page_images,
                temp_path=temp_path,
            ))
        else:
            documents.append(_build_document(
                text, page_num + 1, md5, source_name,
                page_images, False,
            ))

    doc.close()

    if not vision_data:
        return documents

    try:
        groups = []
        unique_data_indices = list(range(len(vision_data)))

        if _DEDUP_ENABLED:
            try:
                for vd in vision_data:
                    vd.phash = vision.compute_image_hash(vd.temp_path)

                groups = []
                for i, vd in enumerate(vision_data):
                    matched = False
                    for rep_idx, indices in groups:
                        dist = VisionService.hamming_distance(
                            vision_data[rep_idx].phash, vd.phash
                        )
                        if dist <= _DEDUP_THRESHOLD:
                            indices.append(i)
                            matched = True
                            break
                    if not matched:
                        groups.append((i, [i]))

                unique_data_indices = [g[0] for g in groups]
            except Exception as e:
                logger.warning(f"【多模态PDF加载·同步】去重失败(跳过): {e}")
                groups = [(i, [i]) for i in range(len(vision_data))]
        else:
            groups = [(i, [i]) for i in range(len(vision_data))]

        batches = []
        for i in range(0, len(unique_data_indices), _BATCH_SIZE):
            batch_indices = unique_data_indices[i:i + _BATCH_SIZE]
            batches.append({
                "image_paths": [vision_data[idx].temp_path for idx in batch_indices],
                "page_numbers": [vision_data[idx].page_num for idx in batch_indices],
                "texts": [vision_data[idx].text for idx in batch_indices],
                "data_indices": batch_indices,
            })

        if batches:
            max_workers = min(len(batches), os.cpu_count() or 4)
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                futures = [
                    pool.submit(
                        vision.describe_pages_batch_sync,
                        b["image_paths"], b["page_numbers"], b["texts"],
                    )
                    for b in batches
                ]
                all_results = [f.result() for f in futures]

            for batch, result in zip(batches, all_results):
                for data_idx in batch["data_indices"]:
                    pn = vision_data[data_idx].page_num
                    vision_data[data_idx].vision_text = result.get(pn, "")

        if _DEDUP_ENABLED:
            for rep_idx, indices in groups:
                rep_text = vision_data[rep_idx].vision_text
                if not rep_text:
                    continue
                for idx in indices:
                    if idx != rep_idx:
                        vision_data[idx].vision_text = rep_text

        for vd in vision_data:
            if vd.text and vd.vision_text:
                content = f"{vd.text}\n\n[页面视觉描述]: {vd.vision_text}"
            elif vd.vision_text:
                content = vd.vision_text
            else:
                content = vd.text

            documents.append(_build_document(
                content, vd.page_num, md5, source_name,
                vd.image_paths, vd.has_images,
            ))

    finally:
        for tp in temp_files:
            try:
                if os.path.exists(tp):
                    os.unlink(tp)
            except Exception:
                pass

    documents.sort(key=lambda d: d.metadata["page"])
    return documents
