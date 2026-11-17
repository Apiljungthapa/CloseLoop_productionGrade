import time
import uuid
from contextlib import contextmanager


def new_request_id() -> str:
    return uuid.uuid4().hex[:12]


@contextmanager
def timer():
    """Yield a callable returning elapsed milliseconds."""
    start = time.perf_counter()
    yield lambda: (time.perf_counter() - start) * 1000
