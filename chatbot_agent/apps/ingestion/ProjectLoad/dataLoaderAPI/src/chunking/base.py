"""
Parent class and shared pieces of the chunking package.

- RecursiveCharacterSplitter: character-based splitter for fallback and
  oversized-section splitting
- BaseChunker: parent of HTMLChunker / XMLChunker. Children only implement
  extract_sections(); everything common lives here: token counting,
    chunk building, and packing sections under the configured token limit.
"""
import re
from abc import ABC, abstractmethod
from typing import List, Tuple

import tiktoken
from bs4 import BeautifulSoup
from langchain_text_splitters import RecursiveCharacterTextSplitter as LangchainRecursiveCharacterTextSplitter
from loguru import logger

from utils.constants import MAX_CHUNK_TOKEN_COUNT


class RecursiveCharacterSplitter:
    """Wrapper around Langchain's RecursiveCharacterTextSplitter."""

    def __init__(
        self,
        seperators: List[str] = ["\n\n", "\n", "\n\\*\\*Note:\\*\\*", " ", ""],
        keep_seperator: bool = True,
        is_separator_regex: bool = False,
        chunk_size: int = 2000,
        chunk_overlap: int = 300,
        **kwargs
    ) -> None:
        self.text_splitter = LangchainRecursiveCharacterTextSplitter(
            separators=seperators,
            keep_separator=keep_seperator,
            is_separator_regex=is_separator_regex,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            **kwargs
        )

    def split_text(self, text: str) -> List[str]:
        return self.text_splitter.split_text(text)


class BaseChunker(ABC):
    """
    Parent section-wise chunker.

    Children implement extract_sections() returning the main title and the
    (section_name, section_content) pairs. chunk_content() packs those
    sections into chunks under MAX_CHUNK_TOKEN_COUNT, splitting
    oversized sections further with the given text splitter.
    """

    @abstractmethod
    def extract_sections(self, soup: BeautifulSoup) -> Tuple[str, List[Tuple[str, str]]]:
            """Return (main_title, [(section_name, section_content), ...])."""
            raise NotImplementedError()

    @staticmethod
    def for_document_type(document_type: str) -> "BaseChunker | None":
        """Return the chunker for the given document type (None if unsupported)."""
        from chunking.html_chunker import HTMLChunker
        from chunking.xml_chunker import XMLChunker

        file_type = (document_type or "").lower()
        if file_type in ("html", "htm"):
            return HTMLChunker(file_type)
        if file_type == "xml":
            return XMLChunker()
        if file_type == "pdf":
            return PDFChunker()
        logger.info(f"No section chunker for document type '{document_type}'; using the generic text splitter")
        return None


    @staticmethod
    def count_tokens(text: str) -> int:
        """Count tokens using the cl100k_base tokenizer."""
        MAX_TOKENIZE_CHARS = 100000  # safeguard limit, adjust as needed
        if len(text) > MAX_TOKENIZE_CHARS:
            logger.error(f"Input text too large for tokenization: {len(text)} characters. Returning 0 tokens.")
            return 100000
        try:
            encoding = tiktoken.get_encoding("cl100k_base")
            base_tokens = len(encoding.encode(text))

            return base_tokens
        except Exception as e:
            logger.error(f"Error in count_tokens: {str(e)} | Type: {type(e).__name__}")
            return 100000

    @staticmethod
    def _make_chunk(main_title: str, section_name: str, text: str, parent_content: str) -> dict:
        return {
            "main_title": main_title,
            "section_name": section_name,
            "text": main_title + "\n" + text.strip(),
            "parent_content": parent_content if parent_content else None,
        }

    def chunk_content(self, content: str, text_splitter, parent_content: str = None) -> List[dict]:
        """Parse the document and pack its sections into token-limited chunks."""
        try:
            soup = BeautifulSoup(content, "html.parser")
            main_title, sections = self.extract_sections(soup)
            logger.info(f"Extracted {len(sections)} sections from {type(self).__name__}")
            if not sections:
                return []
            return self._pack_sections(main_title, sections, text_splitter, parent_content)
        except Exception as e:
            logger.error(f"Error in {type(self).__name__}.chunk_content: {str(e)} | Type: {type(e).__name__}")
            return []

    def _pack_sections(self, main_title: str, sections: list, text_splitter, parent_content: str) -> List[dict]:
        """Combine (section_name, section_content) pairs into token-limited chunks."""
        chunks = []
        current_chunk = ""
        # Names of the sections packed into current_chunk (kept on the chunk)
        current_names = []

        def joined_names() -> str:
            return ", ".join(name for name in current_names if name)

        for section_name, section_content in sections:
            test_chunk = current_chunk + ("\n\n" if current_chunk else "") + section_content

            if self.count_tokens(test_chunk) > MAX_CHUNK_TOKEN_COUNT and current_chunk:
                # Current chunk would exceed the limit: save it and start a new one
                chunks.append(self._make_chunk(main_title, joined_names(), current_chunk, parent_content))
                current_chunk = section_content
                current_names = [section_name]
            else:
                current_chunk = test_chunk
                current_names.append(section_name)

            # A single section can still exceed the limit: split it further
            if self.count_tokens(current_chunk) > MAX_CHUNK_TOKEN_COUNT:
                logger.info(
                    f"Single section exceeds token limit ({self.count_tokens(current_chunk)} > "
                    f"{MAX_CHUNK_TOKEN_COUNT}). Splitting further..."
                )
                for piece in text_splitter.split_text(current_chunk):
                    chunks.append(self._make_chunk(main_title, joined_names(), piece, parent_content))
                current_chunk = ""
                current_names = []

        # Add any remaining content in the current chunk
        if current_chunk.strip():
            chunks.append(self._make_chunk(main_title, joined_names(), current_chunk, parent_content))

        return chunks


class PDFChunker(BaseChunker):
    """Fallback section chunker for plain-text PDF content."""

    def extract_sections(self, soup: BeautifulSoup) -> Tuple[str, List[Tuple[str, str]]]:
        text = soup.get_text("\n", strip=True)
        if not text:
            return "", []
        return "PDF Document", [("PDF Document", text)]
