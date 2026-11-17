"""
Data ingestion configuration module.

This module provides the source selection and chunking configuration used by
the local document preprocessing pipeline.
"""
import os
from dataclasses import dataclass, field
from typing import Any

from config import batch_config, chunking_config_data
from utils.constants import (
    CATEGORY_CODES,
    FILE_FORMAT_CODES,
)

@dataclass
class DataIngestionConfig:
    """
    Configuration for data ingestion jobs.

    This class manages source selection and chunking configuration.

    Attributes:
        source_name: Source system identifier (e.g., "HEALTHDOCS")
        categories: List of category codes to process
        file_formats: List of file format codes (xml | html)
    """
    
    source_name: str = os.getenv("source_name", "HEALTHDOCS")
    categories: list[str] = field(default_factory=list)
    file_formats: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Initialize the source filters after dataclass initialization."""
        self._parse_categories()
        self._parse_file_formats()

    @property
    def chunking(self) -> dict[str, Any]:
        """
        Access chunking configuration.

        Provides access to chunking settings loaded from YAML config,
        including chunk sizes, overlap settings, etc.

        Returns:
            Dictionary containing chunking configuration

        Chunking values are read from the project's YAML configuration.
        """
        return chunking_config_data

    def _parse_categories(self) -> None:
        """Parse CATEGORY (dev.env or env var) into a list of valid category codes.

        Falls back to all CATEGORY_CODES when empty.
        """
        raw_categories = batch_config.CATEGORY
        if raw_categories:
            self.categories = [
                c.strip() for c in raw_categories.split(",")
                if c.strip() in CATEGORY_CODES
            ]
        else:
            self.categories = list(CATEGORY_CODES)

    def _parse_file_formats(self) -> None:
        """Parse FILE_FORMAT (dev.env or env var) into a list of valid format codes.

        Falls back to all FILE_FORMAT_CODES when empty.
        """
        raw_formats = batch_config.FILE_FORMAT
        if raw_formats:
            self.file_formats = [f.strip() for f in raw_formats.split(",") if f.strip() in FILE_FORMAT_CODES]
        else:
            self.file_formats = list(FILE_FORMAT_CODES)

