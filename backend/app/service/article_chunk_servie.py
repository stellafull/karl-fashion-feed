"""Pure-text markdown chunking helpers for article retrieval."""

from __future__ import annotations

from typing import Any

from langchain.text_splitter import RecursiveCharacterTextSplitter


def normalize_markdown(md: str) -> str:
    """Normalize article markdown before chunking."""
    return "\n".join(line.rstrip() for line in md.replace("\r\n", "\n").replace("\r", "\n").splitlines()).strip()


def build_text_splitter(
    chunk_size: int = 1000,
    chunk_overlap: int = 200,
) -> RecursiveCharacterTextSplitter:
    return RecursiveCharacterTextSplitter.from_tiktoken_encoder(
        encoding_name="cl100k_base",
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=[
            "\n\n",
            "\n",
            "## ",
            "# ",
            "。",
            "！",
            "？",
            "；",
            "，",
            "、",
            ",",
            ".",
            " ",
            "",
        ],
    )


def build_text_chunks(
    markdown_text: str,
    source_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
) -> list[dict[str, Any]]:
    splitter = build_text_splitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    docs = splitter.create_documents([normalize_markdown(markdown_text)], metadatas=[{"source_id": source_id}])

    chunks: list[dict[str, Any]] = []
    for idx, doc in enumerate(docs):
        start_index = doc.metadata.get("start_index", -1)
        end_index = start_index + len(doc.page_content) if start_index >= 0 else -1
        chunks.append(
            {
                "chunk_id": f"{source_id}:text:{idx}",
                "type": "text",
                "order": idx,
                "source_id": source_id,
                "page_content": doc.page_content,
                "metadata": {
                    "modality": "text",
                    "chunk_index": idx,
                    "text_start": start_index,
                    "text_end": end_index,
                    "prev_chunk_id": f"{source_id}:text:{idx - 1}" if idx > 0 else None,
                    "next_chunk_id": None,
                },
            }
        )

    for idx, chunk in enumerate(chunks[:-1]):
        chunk["metadata"]["next_chunk_id"] = chunks[idx + 1]["chunk_id"]
    return chunks


def split_markdown_into_text_chunks(
    md: str,
    source_id: str,
    chunk_size: int = 500,
    chunk_overlap: int = 80,
) -> list[dict[str, Any]]:
    """Split pure-text markdown into retrieval chunks."""
    return build_text_chunks(
        markdown_text=md,
        source_id=source_id,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
