from pathlib import Path
from typing import Dict, Any

from utils.file_reader import FileReader
from .schema import create_metadata_model
from loguru import logger


class SourceLoaderConfig:
    # Metadata models are built once per source and reused across instances
    _metadata_model_cache: Dict[str, Any] = {}

    def __init__(self, source_name: str, config_path: str = str(Path(__file__).resolve().parent / "config" / "config.yaml")):

        config_data = FileReader(config_path).get_config()

        self.source_data = config_data.get("sources", {}).get(source_name, {})
        if not self.source_data:
            raise ValueError(f"No configuration found for source: {source_name}")

        self.source_name = source_name
        self.payloads = self.source_data.get("payload", {})

    def load_metadata_schema_from_config(self):
        """
        Load metadata fields dynamically from source-specific configuration.
        The built model is cached per source so repeated calls are silent no-ops.
        """
        cached = self._metadata_model_cache.get(self.source_name)
        if cached is not None:
            return cached

        logger.info(f"Loading metadata schema from config file for source : {self.source_name}")
        metadata_fields = self.source_data.get("metadata", {})

        Metadata = create_metadata_model(metadata_fields)
        self._metadata_model_cache[self.source_name] = Metadata
        return Metadata

    def get_payload(self, payload_type: str, extra_params: Dict[str, Any] = None, additional_fields: Dict[str, Any] = None) -> Dict:
        try:
            payload = dict(self.payloads.get(payload_type, {}))

            # Strip category / fileFormat filters from the payload when not provided
            # (analog of stripping lob/jurisdiction from the CLM filterQuery)
            if not extra_params or 'category' not in extra_params:
                payload.pop("category", None)
            if not extra_params or 'fileFormat' not in extra_params:
                payload.pop("fileFormat", None)

            if extra_params is not None:
                for key, value in payload.items():
                    if isinstance(value, str):  # Only process strings with placeholders
                        payload[key] = value.format(**extra_params)

            # Add extra dynamic parameters that aren't placeholders
            if additional_fields:
                payload.update(additional_fields)

            return payload

        except Exception as e:
            logger.exception(f"Error generating payload for type {payload_type}: {e}")
            return {}
