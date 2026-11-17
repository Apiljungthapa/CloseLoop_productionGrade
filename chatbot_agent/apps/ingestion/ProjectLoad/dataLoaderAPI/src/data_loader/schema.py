from pydantic import BaseModel, create_model
from typing import Optional, List, Dict, Any, Tuple
from datetime import datetime

TYPE_MAP = {
    "str": str,
    "int": int,
    "bool": bool,
    "datetime": datetime,
    "float": float
}


def get_field_type(field_name: str, field_def: any) -> Tuple[Any, Any]:
    if isinstance(field_def, list) and field_def:
        type_token = field_def[0]
        if isinstance(type_token, dict):
            return (List[Optional[dict]], [])
        py_type = TYPE_MAP.get(type_token, str)
        return (List[Optional[py_type]], [])
    return (Optional[TYPE_MAP.get(field_def, str)], None)


def create_metadata_model(metadata_fields):
    fields = {}
    for field_name, field_def in metadata_fields.items():
        fields[field_name] = get_field_type(field_name, field_def)
    fields['version_history'] = (Optional[Dict[str, Any]], None)
    return create_model("Metadata", **fields)


class DocumentSchema(BaseModel):
    id: Optional[str] = None
    text: Optional[str] = None
    processed_text: Optional[str] = None
    file_path: str
    metadata: Optional[BaseModel] = None
