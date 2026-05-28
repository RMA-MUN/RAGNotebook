import os
import shutil

import fitz

from app.core.logger_handler import logger
from app.utils.path_tool import get_abstract_path, get_data_path

# 存储结构：data/extracted_images/{user_id}/{md5}/
# 这样组织的好处：
# 1. 按用户隔离——不同用户之间的图片互不可见
# 2. 按文档 MD5 隔离——删除文档时可以直接删除整个 md5 目录
# 3. 路径中包含用户ID，便于图片鉴权时验证权限


def get_image_storage_dir(user_id: str, md5: str) -> str:
    """获取图片存储目录 data/extracted_images/{user_id}/{md5}/"""
    base_dir = os.path.join(get_data_path(), 'extracted_images')
    storage_dir = os.path.join(base_dir, user_id, md5)
    os.makedirs(storage_dir, exist_ok=True)
    return storage_dir


def extract_images_from_pdf(pdf_path: str, user_id: str, md5: str) -> dict[int, list[str]]:
    """
    从PDF中提取所有嵌入的原始图片，保存到 data/extracted_images/{user_id}/{md5}/

    使用 PyMuPDF（fitz）的 extract_image 方法提取每个图片对象的原始字节流，
    而不是进行屏幕截图/栅格化，因此可以保持图片的原始分辨率。

    图片命名规则：p{page_num}_i{img_idx}.{ext}
    - page_num: 页码（从0开始）
    - img_idx: 该页中的图片序号
    - ext: 原始格式（png/jpg/tiff 等）

    Args:
        pdf_path: PDF文件的绝对路径
        user_id: 用户ID
        md5: 文件的MD5值

    Returns:
        {page_num: [相对图片文件名列表]}
    """
    abs_pdf_path = get_abstract_path(pdf_path) if not os.path.isabs(pdf_path) else pdf_path
    if not os.path.exists(abs_pdf_path):
        logger.error(f"【图片提取】PDF文件不存在: {abs_pdf_path}")
        return {}

    output_dir = get_image_storage_dir(user_id, md5)
    result = {}

    try:
        doc = fitz.open(abs_pdf_path)
    except Exception as e:
        logger.error(f"【图片提取】打开PDF失败: {e}")
        return {}

    for page_num in range(len(doc)):
        page = doc[page_num]
        # get_images(full=True) 返回该页所有图片引用列表（包括嵌套在 Form XObject 中的图片）
        images = page.get_images(full=True)
        page_images = []

        for img_idx, img in enumerate(images):
            # img[0] 是这个图片对象的 xref（交叉引用编号），用于 extract_image 定位
            xref = img[0]
            try:
                base_image = doc.extract_image(xref)
                image_bytes = base_image["image"]
                ext = base_image["ext"]
                filename = f"p{page_num}_i{img_idx}.{ext}"
                filepath = os.path.join(output_dir, filename)

                with open(filepath, "wb") as f:
                    f.write(image_bytes)

                page_images.append(filename)
            except Exception as e:
                logger.warning(f"【图片提取】提取第{page_num}页第{img_idx}张图片失败: {e}")
                continue

        result[page_num] = page_images

    doc.close()
    total_images = sum(len(v) for v in result.values())
    logger.info(f"【图片提取】从PDF中提取了 {total_images} 张图片, 保存至: {output_dir}")
    return result


def delete_image_directory(user_id: str, md5: str) -> bool:
    """
    删除指定用户和md5的图片目录。
    当用户删除某个文档时，同步清理对应的图片目录，避免残留文件占用磁盘。
    """
    base_dir = os.path.join(get_data_path(), 'extracted_images')
    storage_dir = os.path.join(base_dir, user_id, md5)
    if os.path.exists(storage_dir):
        shutil.rmtree(storage_dir)
        logger.info(f"【图片清理】已删除图片目录: {storage_dir}")
        return True
    return False


def delete_user_all_images(user_id: str) -> bool:
    """
    删除指定用户的所有图片目录。
    当用户清空整个知识库时调用，清理该用户的所有提取图片。
    """
    base_dir = os.path.join(get_data_path(), 'extracted_images')
    user_dir = os.path.join(base_dir, user_id)
    if os.path.exists(user_dir):
        shutil.rmtree(user_dir)
        logger.info(f"【图片清理】已删除用户 {user_id} 的所有图片")
        return True
    return False
