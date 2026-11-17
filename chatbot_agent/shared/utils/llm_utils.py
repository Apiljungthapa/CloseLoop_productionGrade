from typing import Iterable


def normalize_vector(vector: Iterable[float], size: int) -> list[float]:
    """Pad with zeros or truncate so the vector matches the collection size.

    Ingestion pads the same way, so queries must too.
    """
    values = [float(v) for v in vector]
    if len(values) < size:
        values += [0.0] * (size - len(values))
    return values[:size]
