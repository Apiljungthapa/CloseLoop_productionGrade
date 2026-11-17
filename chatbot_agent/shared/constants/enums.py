from enum import Enum


class Route(str, Enum):
    """Where the query gateway sends a question."""

    DOC = "doc"          # answer from document chunks (RAG)
    DB = "db"            # answer from the relational database (text-to-SQL)
    HYBRID = "hybrid"    # both, merged as evidence
    CHITCHAT = "chitchat"  # greetings/small talk, no retrieval


class EvidenceSource(str, Enum):
    DOCUMENT = "document"
    DATABASE = "database"
