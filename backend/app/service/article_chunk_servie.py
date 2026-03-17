"""article chunk service
- 将网页文章切分成多个文本块（chunk），并为每个文本块生成多模态向量和稀疏向量，最后存储到 Milvus 向量数据库中。
"""

from __future__ import annotations
from langchain.text_splitter import RecursiveCharacterTextSplitter
import re
from typing import List, Tuple, Dict, Any

IMAGE_RE = re.compile(r"\[image:([0-9a-fA-F-]{36})]")

def normalize_markdown(md: str) -> str:
    """对 Markdown 文本进行预处理，去除不必要的空白行和特殊字符."""
    md = md.replace("\r\n", "\n").replace("\r", "\n")
    md = re.sub(r"[ \t]+", " ", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


def build_text_splitter(chunk_size: int = 1000, chunk_overlap: int = 200) -> RecursiveCharacterTextSplitter:
    """构建文本切分器. 添加中文符号支持"""
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=[
            "\n\n",
            "\n",
            "## ",
            "# ",
            "。", "！", "？", "；",
            "，", "、", ",", ".",
            " ",
            ""
        ],
    )

def extract_images_and_text(md: str) -> Tuple[str, List[Dict[str, Any]]]:
    """
    返回:
    - text_only: 去掉所有 [image:...] 后的纯文本
    - images: 每张图在 text_only 坐标系里的插入位置
    """
    text_parts = []
    images = []

    last_end = 0
    text_cursor = 0  # 在“去图后的纯文本”里的字符位置

    for m in IMAGE_RE.finditer(md):
        start, end = m.span()
        image_id = m.group(1)

        # 图片前面的文本
        before = md[last_end:start]
        text_parts.append(before)
        text_cursor += len(before)

        images.append({
            "image_id": image_id,
            "token": m.group(0),
            "anchor_text_pos": text_cursor,  # 这张图插在纯文本的哪个字符位置
        })

        last_end = end

    tail = md[last_end:]
    text_parts.append(tail)

    text_only = "".join(text_parts)
    return text_only, images

def build_text_chunks(
    text_only: str,
    source_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
) -> List[Dict[str, Any]]:
    splitter = build_text_splitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    # 用 create_documents 比 split_text 更方便拿到 start_index
    docs = splitter.create_documents(
        [text_only],
        metadatas=[{"source_id": source_id}]
    )

    chunks = []
    for idx, doc in enumerate(docs):
        start_index = doc.metadata.get("start_index")
        # 某些版本默认没有 start_index；下面做个兜底
        if start_index is None:
            # 兜底时只能不保精确 start/end
            start_index = -1
            end_index = -1
        else:
            end_index = start_index + len(doc.page_content)

        chunks.append({
            "chunk_id": f"{source_id}:text:{idx}",
            "type": "text",
            "order": None,  # 后面统一排
            "source_id": source_id,
            "page_content": doc.page_content,
            "metadata": {
                "modality": "text",
                "chunk_index": idx,
                "text_start": start_index,
                "text_end": end_index,
            }
        })

    return chunks

def attach_images_to_stream(
    text_chunks: List[Dict[str, Any]],
    images: List[Dict[str, Any]],
    source_id: str,
) -> List[Dict[str, Any]]:
    """
    按图片 anchor_text_pos，把 image block 插回 ordered stream。
    规则:
    - 如果 anchor 落在某个 text chunk 范围内，则图片排在该 chunk 后面
    - 如果没有精确 start/end，就退化成全部 text 在前、image 在后（不会建议这种情况）
    """
    stream = []
    inserted_image_ids = set()

    for chunk in text_chunks:
        stream.append(chunk)

        start_ = chunk["metadata"].get("text_start", -1)
        end_ = chunk["metadata"].get("text_end", -1)

        if start_ >= 0 and end_ >= 0:
            for img in images:
                if img["image_id"] in inserted_image_ids:
                    continue
                # anchor 落在当前 chunk 内，或恰好在 chunk 末尾
                if start_ <= img["anchor_text_pos"] <= end_:
                    stream.append({
                        "chunk_id": f"{source_id}:image:{len(inserted_image_ids)}",
                        "type": "image",
                        "order": None,
                        "source_id": source_id,
                        "page_content": img["token"],
                        "metadata": {
                            "modality": "image",
                            "image_id": img["image_id"],
                            "anchor_text_pos": img["anchor_text_pos"],
                        }
                    })
                    inserted_image_ids.add(img["image_id"])

    # 兜底：如果某些图片没插进去，挂到最后
    for img in images:
        if img["image_id"] not in inserted_image_ids:
            stream.append({
                "chunk_id": f"{source_id}:image:{len(inserted_image_ids)}",
                "type": "image",
                "order": None,
                "source_id": source_id,
                "page_content": img["token"],
                "metadata": {
                    "modality": "image",
                    "image_id": img["image_id"],
                    "anchor_text_pos": img["anchor_text_pos"],
                }
            })
            inserted_image_ids.add(img["image_id"])

    # 统一补 order / prev / next
    for i, block in enumerate(stream):
        block["order"] = i
        block["metadata"]["prev_chunk_id"] = stream[i - 1]["chunk_id"] if i > 0 else None
        block["metadata"]["next_chunk_id"] = stream[i + 1]["chunk_id"] if i < len(stream) - 1 else None

    return stream

def split_markdown_preserving_images_v2(
    md: str,
    source_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
) -> List[Dict[str, Any]]:
    md = normalize_markdown(md)

    # 1) 先抽图片，拿到“去图后的整篇文本”
    text_only, images = extract_images_and_text(md)

    # 2) 再对整个 text_only 做一次 recursive split
    text_chunks = build_text_chunks(
        text_only=text_only,
        source_id=source_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    # 3) 按 anchor 把 image block 插回 ordered stream
    return attach_images_to_stream(text_chunks, images, source_id)