from abc import ABC, abstractmethod
from typing import List


class TextProcessor(ABC):
    """
    A component for text processing.
    """

    #: File formats this processor can handle (e.g. ["html", "htm"])
    supported_formats: List[str] = []

    @abstractmethod
    def process(self, text: str, **kwargs) -> str:
        return text

    def supports(self, file_format: str) -> bool:
        """Check whether this processor handles the given file format."""
        return (file_format or "").lower() in self.supported_formats

    def __call__(self, *args, **kwargs) -> str:
        return self.process(*args, **kwargs)

    @staticmethod
    def pre_process_document(doc, text_processors: List["TextProcessor"]) -> tuple:
        """Pick the text processor matching the document's file format and run it.

        Sets doc.processed_text and returns (clean_text, extracted_metadata).
        """
        file_format = getattr(doc.metadata, "fileFormat", "") if doc.metadata else ""
        for processor in text_processors:
            if not processor.supports(file_format):
                continue
            clean_parent_for_section, metadata = processor.process(doc.text, doc=doc)
            doc.processed_text = clean_parent_for_section
            return clean_parent_for_section, metadata
        return doc.processed_text, {}
