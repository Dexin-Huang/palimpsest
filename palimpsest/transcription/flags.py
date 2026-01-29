import json
from pathlib import Path
from typing import Any


def load_page_flags(out_dir: Path) -> dict[str, Any]:
    flags_path = out_dir / "page_flags.json"
    if not flags_path.exists():
        return {"skip_pass2": {}}
    data = json.loads(flags_path.read_text(encoding="utf-8"))
    skip_pass2 = data.get("skip_pass2", {})
    if isinstance(skip_pass2, list):
        skip_pass2 = {item: "manual" for item in skip_pass2}
    if not isinstance(skip_pass2, dict):
        skip_pass2 = {}
    return {"skip_pass2": skip_pass2}
