import json
from pathlib import Path

HERE = Path(__file__).parent
PDF_BYTES = (HERE / "text_3pages.pdf").read_bytes()
TRUTH = json.loads((HERE / "text_3pages.json").read_text())
