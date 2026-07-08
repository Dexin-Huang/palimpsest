"""The prompt store: every prompt is a file, loaded and hashed here.

Prompt names are slash paths under ``factory/prompts/``, mirroring the
station/language structure: ``read/la/diplomatic_json`` resolves to
``prompts/read/la/diplomatic_json.txt``. The content hash travels into every
stage run's provenance record, so "which exact prompt produced this page"
is always answerable.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from palimpsest.factory.config import PROMPTS_DIR


@dataclass(frozen=True)
class Prompt:
    name: str
    text: str
    sha256: str


def load(name: str, root: Path = PROMPTS_DIR) -> Prompt:
    path = (root / f"{name}.txt").resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Prompt name escapes the prompt store: {name}")
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")
    text = path.read_text(encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return Prompt(name=name, text=text, sha256=digest)
