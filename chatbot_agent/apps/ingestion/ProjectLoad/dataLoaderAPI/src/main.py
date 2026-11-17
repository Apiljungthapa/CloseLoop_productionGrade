import argparse
import json
import os
from pathlib import Path

from loguru import logger

from chunking import RecursiveCharacterSplitter
from config import batch_config, chunking_config_data
from core.data_ingestion_config import DataIngestionConfig
from data_loader.loader import DataLoader
from data_loader.local_loader import load_local_document
from processors.document_preprocessor import DocumentPreprocessor
from processors.text_processors import HtmlToTextProcessor, XmlToTextProcessor
from utils.health_check import perform_source_api_health_check
from vector_store.gemini_embedder import GeminiEmbedder
from vector_store.qdrant_store import QdrantVectorStore


class DataLoadOrchestrator:
    """Search, download, clean, and chunk source documents."""

    def __init__(self) -> None:
        self.config = DataIngestionConfig()
        self.data_loader = DataLoader(
            batch_config.SOURCE_BASE_URL,
            batch_config.SOURCE_SEARCH_URL,
            batch_config.SOURCE_DOWNLOAD_URL,
        )
        splitter_config = chunking_config_data["chunking"]["recursive_text_splitter"]
        embedding_config = chunking_config_data.get("embedding", {})
        qdrant_config = chunking_config_data.get("qdrant", {})
        self.embedding_model = GeminiEmbedder(
            os.environ.get("GEMINI_API_KEY", ""),
            os.environ.get("EMBEDDING_MODEL") or embedding_config.get("model_name", "gemini-embedding-001"),
            qdrant_config.get("vector_size", 512),
        )
        self.qdrant_store = QdrantVectorStore(
            collection_name=os.environ.get("QDRANT_COLLECTION") or qdrant_config.get("collection_name", "rag_documents"),
            vector_size=qdrant_config.get("vector_size", 512),
            url=qdrant_config.get("cluster_endpoint"),
            api_key=qdrant_config.get("api_key"),
            local_path=qdrant_config.get("local_path", "./qdrant_storage"),
        )
        self.processor = DocumentPreprocessor(
            config=self.config,
            data_loader=self.data_loader,
            text_splitter=RecursiveCharacterSplitter(
                chunk_size=splitter_config["chunk_size"],
                chunk_overlap=splitter_config["chunk_overlap"],
            ),
            text_processors=[HtmlToTextProcessor(), XmlToTextProcessor()],
        )

    def add_embeddings(self, chunks: list[dict]) -> list[dict]:
        """Create embeddings for each chunk text and attach them to the chunk payload."""
        valid_chunks = [(index, chunk) for index, chunk in enumerate(chunks) if str(chunk.get("text", "")).strip()]
        if not valid_chunks:
            return chunks

        texts = [chunk["text"] for _, chunk in valid_chunks]
        vectors = self.embedding_model.encode_documents(texts)

        for (index, chunk), vector in zip(valid_chunks, vectors):
            chunk_vector = [float(value) for value in vector]
            if len(chunk_vector) != self.qdrant_store.vector_size:
                chunk_vector = self.qdrant_store._normalize_vector(chunk_vector, self.qdrant_store.vector_size)
            chunks[index]["embedding"] = chunk_vector

        return chunks

    def store_in_qdrant(self, chunks: list[dict], strict: bool = False) -> list[dict]:
        """Persist chunk embeddings and metadata to Qdrant. strict=True raises so a queue worker can retry."""
        if not chunks:
            return chunks
        try:
            self.qdrant_store.upsert_chunks(chunks)
        except Exception as error:
            if strict:
                raise
            logger.exception(f"Failed to store chunks in Qdrant: {error}")
        return chunks

    def run_data_load(self) -> tuple[list[dict], int, int, list[dict]]:
        """Return chunks, source record count, downloaded count, and failures."""
        all_chunks = []
        all_failures = []
        source_count = 0
        downloaded_count = 0

        for file_format in self.config.file_formats:
            for category in self.config.categories:
                total, documents = self.processor.get_documents(file_format, category)
                source_count += total
                logger.info(
                    f"Found {len(documents)}/{total} {file_format} documents in {category}"
                )
                chunks, failures, downloaded = self.processor.process_documents(documents)
                chunks_with_embeddings = self.add_embeddings(chunks)
                self.store_in_qdrant(chunks_with_embeddings)
                all_chunks.extend(chunks_with_embeddings)
                all_failures.extend(failures)
                downloaded_count += downloaded

        return all_chunks, source_count, downloaded_count, all_failures


    def run_local_file(
        self, file_path: str, strict: bool = False, tenant_id: str | None = None, uploaded_by: str | None = None
    ) -> list[dict]:
        """Load one local file by its detected type, then clean and chunk it.

        With ``tenant_id`` the document belongs to that company only: its id is scoped to the tenant
        (two companies can upload the same file name) and every chunk carries the tenant. Without it
        the document is shared reference material (the CLI path)."""
        document = load_local_document(file_path)
        if tenant_id:
            document.id = f"{tenant_id}__{Path(file_path).name}"
        chunks = self.processor.chunk_document(document)
        for chunk in chunks:
            chunk["tenant_id"] = tenant_id
            chunk["uploaded_by"] = uploaded_by
        chunks_with_embeddings = self.add_embeddings(chunks)
        if tenant_id and chunks_with_embeddings:
            # Re-uploading a file replaces it: otherwise the tail of a longer old version would linger.
            self.qdrant_store.delete_document(document.id, tenant_id)
        self.store_in_qdrant(chunks_with_embeddings, strict=strict)

        return chunks_with_embeddings
    


def write_chunks(chunks: list[dict], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8") as output_file:
        for chunk in chunks:
            output_file.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    

def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch, clean, and chunk source documents.")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/chunks.jsonl"),
        help="JSONL output path (default: output/chunks.jsonl)",
    )
    parser.add_argument(
        "--file",
        help="Local file to process instead of the source API (prompted if omitted and --api is not set)",
    )
    parser.add_argument("--api", action="store_true", help="Fetch from the source API")
    args = parser.parse_args()

    if not args.api:
        file_path = args.file or input("Enter file path: ").strip().strip("'\"")
        chunks = DataLoadOrchestrator().run_local_file(file_path)
        write_chunks(chunks, args.output)
        logger.info(f"{len(chunks)} chunks written to {args.output}")
        return

    if not perform_source_api_health_check():
        raise SystemExit("Source API health check failed.")

    chunks, source_count, downloaded_count, failures = DataLoadOrchestrator().run_data_load()
    write_chunks(chunks, args.output)

    logger.info(
        f"Finished: {source_count} source records, {downloaded_count } documents downloaded, "
        f"{len(chunks)} chunks written to {args.output}, {len(failures)} failures."
    )
    if failures:
        for failure in failures:
            logger.warning(f"Document failure: {failure}")


if __name__ == "__main__":
    main()
