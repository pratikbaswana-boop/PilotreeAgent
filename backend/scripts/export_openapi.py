"""Export FastAPI's schema without starting the server or connecting to the DB."""
import json
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))
from app.main import app  # noqa: E402

(root.parent / "frontend" / "openapi.json").write_text(
    json.dumps(app.openapi(), indent=2) + "\n"
)
