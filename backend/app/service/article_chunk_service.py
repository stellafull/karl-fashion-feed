"""Simple markdown chunking helpers for article retrieval."""

from __future__ import annotations

from typing import Any

from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

HEADERS_TO_SPLIT_ON: list[tuple[str, str]] = [
    ("#", "h1"),
    ("##", "h2"),
    ("###", "h3"),
    ("####", "h4"),
    ("#####", "h5"),
    ("######", "h6"),
]

MULTILINGUAL_SEPARATORS: list[str] = [
    "\n\n",
    "\n",
    "。\n",
    "！\n",
    "？\n",
    "；\n",
    "。\u3000",
    "！\u3000",
    "？\u3000",
    "；\u3000",
    "。",
    "！",
    "？",
    "；",
    "：",
    "，",
    "、",
    ". ",
    "! ",
    "? ",
    "; ",
    ": ",
    ", ",
    " ",
    "",
]


def normalize_markdown(markdown_text: str) -> str:
    return "\n".join(
        line.rstrip()
        for line in markdown_text.replace("\r\n", "\n").replace("\r", "\n").splitlines()
    ).strip()


def split_markdown_into_text_chunks(
    md: str,
    source_id: str,
    chunk_size: int = 800,
    chunk_overlap: int = 150,
) -> list[dict[str, Any]]:
    normalized_markdown = normalize_markdown(md)
    if not normalized_markdown:
        return []

    header_splitter = MarkdownHeaderTextSplitter(
        headers_to_split_on=HEADERS_TO_SPLIT_ON,
        strip_headers=True,
    )
    section_docs = header_splitter.split_text(normalized_markdown)
    if not section_docs:
        return []

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=MULTILINGUAL_SEPARATORS,
        keep_separator="end",
    )

    chunks: list[dict[str, Any]] = []
    for section_index, section_doc in enumerate(section_docs):
        section_text = section_doc.page_content.strip()
        if not section_text:
            continue

        heading_path = _build_heading_path(section_doc.metadata)
        search_offset = 0
        for piece in text_splitter.split_text(section_text):
            page_content = piece.strip()
            if not page_content:
                continue

            local_start = section_text.find(page_content, search_offset)
            if local_start < 0:
                local_start = section_text.find(page_content)
            if local_start < 0:
                local_start = 0
            search_offset = local_start + len(page_content)

            chunk_index = len(chunks)
            chunk_id = f"{source_id}:text:{chunk_index}"
            chunks.append(
                {
                    "chunk_id": chunk_id,
                    "type": "text",
                    "order": chunk_index,
                    "source_id": source_id,
                    "page_content": page_content,
                    "metadata": {
                        "modality": "text",
                        "chunk_index": chunk_index,
                        "section_index": section_index,
                        "heading_path": heading_path,
                        "text_start": local_start,
                        "text_end": local_start + len(page_content),
                        "prev_chunk_id": chunks[-1]["chunk_id"] if chunks else None,
                        "next_chunk_id": None,
                    },
                }
            )

            if chunk_index > 0:
                chunks[chunk_index - 1]["metadata"]["next_chunk_id"] = chunk_id

    return chunks


def _build_heading_path(metadata: dict[str, Any]) -> list[str]:
    heading_path: list[str] = []
    for level in ("h2", "h3", "h4", "h5", "h6"):
        value = metadata.get(level)
        if isinstance(value, str) and value.strip():
            heading_path.append(value.strip())
    return heading_path
