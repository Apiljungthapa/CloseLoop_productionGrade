from typing import Dict, List, Optional
from loguru import logger
from datetime import datetime
from pydantic import BaseModel

from .schema import DocumentSchema


class DataParser:

    @staticmethod
    def parse_datetime(date_str: str) -> Optional[datetime]:
        """Convert date string to datetime object."""
        if not date_str:
            return None
        date_formats = [
            "%Y-%m-%d",                  # Example: 2024-01-27
            "%Y-%m-%dT%H:%M:%S",         # Example: 2024-01-27T09:30:00
            "%Y-%m-%dT%H:%M:%SZ",        # Example: 2024-01-27T09:30:00Z
        ]
        for fmt in date_formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        logger.warning(f"Failed to parse datetime: {date_str}")
        return None

    @staticmethod
    def build_metadata(doc: dict, MetadataModel: BaseModel) -> BaseModel:
        """
        Dynamically builds a Metadata instance by iterating over the fields defined
        in the dynamic MetadataModel and assigning values from 'doc'.
        :param doc:
        :param MetadataModel:
        :return:
        """
        metadata_values = {}

        for field_name, field_type in MetadataModel.__annotations__.items():

            if "datetime" in str(field_type):
                # check doc.get(field_name) is datetime object
                if isinstance(doc.get(field_name), datetime):
                    metadata_values[field_name] = doc.get(field_name)
                else:
                    metadata_values[field_name] = DataParser.parse_datetime(doc.get(field_name))
            else:
                metadata_values[field_name] = doc.get(field_name)

        return MetadataModel(**metadata_values)

    @staticmethod
    def parse_search_response(response: Dict, MetadataSchema) -> List[DocumentSchema]:

        documents = []
        docs = response.get('records', [])

        for doc in docs:
            metadata = DataParser.build_metadata(doc, MetadataSchema)
            document = DocumentSchema(
                id=doc.get('dataId') if doc.get("dataId") else "",
                text="",
                processed_text="",
                file_path=doc.get("storagePath") if doc.get("storagePath") else "",
                metadata=metadata,
            )

            documents.append(document)

        return documents
