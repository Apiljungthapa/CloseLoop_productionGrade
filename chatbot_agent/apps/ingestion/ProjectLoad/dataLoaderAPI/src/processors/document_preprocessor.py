"""Fetch, clean, and chunk source documents without indexing infrastructure."""
import asyncio

from loguru import logger

from chunking import BaseChunker, RecursiveCharacterSplitter
from core.data_ingestion_config import DataIngestionConfig
from data_loader.loader import DataLoader
from data_loader.schema import DocumentSchema
from processors.base import TextProcessor
from utils.constants import MAX_CHUNK_TOKEN_COUNT


class DocumentPreprocessor:
    """Coordinate source API retrieval and local document preprocessing."""

    def __init__(
        self,
        config: DataIngestionConfig,
        data_loader: DataLoader,
        text_splitter: RecursiveCharacterSplitter,
        text_processors: list[TextProcessor],
    ) -> None:
        self.config = config
        self.data_loader = data_loader
        self.text_splitter = text_splitter
        self.text_processors = text_processors
        chunking_config = config.chunking.get("chunking", {})
        self.section_chunking = chunking_config.get("section_chunking") in ("true", True)

    def get_documents(
        self, file_format: str | None = None, category: str | None = None
    ) -> tuple[int, list[DocumentSchema]]:
        """Search the configured source API for matching document metadata."""
        extra_params = {}
        if file_format:
            extra_params["fileFormat"] = file_format
        if category:
            extra_params["category"] = category

        return asyncio.run(
            self.data_loader.search_document_async(
                source_name=self.config.source_name,
                extra_params=extra_params,
                additional_fields={"limit": "10"},
            )
        )

    def process_documents(
        self, documents: list[DocumentSchema]
    ) -> tuple[list[dict], list[dict], int]:
        """Download source content, preprocess it, and return serializable chunks."""
        if not documents:
            return [], [], 0

        downloaded, _download_failed_names, failures = asyncio.run(
            self.data_loader.download_documents(
                self.config.source_name,
                {"documents": documents},
            )
        )
        chunk_records = []

        for document in downloaded:
            try:
                chunk_records.extend(self.chunk_document(document))
            except Exception as error:
                logger.exception(f"Preprocessing failed for document {document.id}: {error}")
                failures.append({"doc_id": document.id, "error": str(error)})

        return chunk_records, failures, len(downloaded)

    def chunk_document(self, document: DocumentSchema) -> list[dict]:
        """Clean one downloaded document and split it into local chunk records."""
        metadata = document.metadata.model_dump(mode="json") if document.metadata else {}
        cleaned_text, extracted_metadata = TextProcessor.pre_process_document(
            document, self.text_processors
        )
        cleaned_text = (cleaned_text or "").strip()
        logger.info(f"[1/3 clean] {document.id}: {len(cleaned_text)} chars after text processing")
        if not cleaned_text:
            return []

        metadata.update(extracted_metadata)
        file_format = metadata.get("fileFormat", "")

        total_tokens = BaseChunker.count_tokens(cleaned_text)
        logger.info(f"[2/3 size] {total_tokens} tokens (limit {MAX_CHUNK_TOKEN_COUNT})")
        if total_tokens <= MAX_CHUNK_TOKEN_COUNT:
            logger.info("[3/3 chunk] under limit -> single chunk")
            chunks = [{
                "main_title": metadata.get("title", ""),
                "section_name": "",
                "text": cleaned_text,
                "parent_content": cleaned_text,
            }]
        else:
            chunks = []
            if self.section_chunking:
                chunker = BaseChunker.for_document_type(file_format)
                if chunker:
                    logger.info(f"[3/3 chunk] section chunking with {type(chunker).__name__}")
                    chunks = chunker.chunk_content(
                        document.text or "",
                        self.text_splitter,
                        parent_content=cleaned_text,
                    )

            if not chunks:
                logger.info("[3/3 chunk] fallback -> RecursiveCharacterSplitter")
                chunks = [
                    {
                        "main_title": metadata.get("title", ""),
                        "section_name": "",
                        "text": piece,
                        "parent_content": cleaned_text,
                    }
                    for piece in self.text_splitter.split_text(cleaned_text)
                ]

        return [
            {
                "document_id": document.id,
                "record_id": metadata.get("recordId"),
                "file_path": document.file_path,
                "chunk_id": chunk_id,
                "total_chunks": len(chunks),
                "text": chunk["text"],
                "main_title": chunk.get("main_title", ""),
                "section_name": chunk.get("section_name", ""),
                "parent_content": chunk.get("parent_content"),
                "metadata": metadata,
            }
            for chunk_id, chunk in enumerate(chunks, start=1)
        ]