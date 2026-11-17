import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
GATEWAY_SRC = REPO_ROOT / "apps" / "query_gateway" / "src"
for path in (GATEWAY_SRC, REPO_ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
