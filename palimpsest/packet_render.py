from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from palimpsest.config import DEFAULT_TECTONIC_BIN
from palimpsest.edition_fonts import resolve_edition_font_policy
from palimpsest.models import PagePacket
from palimpsest.packet_scholar import repair_packet_json
from palimpsest.page_packet import sync_packet_edition_template


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _find_tectonic(explicit_engine: str | None = None) -> Path:
    candidates: list[str] = []
    if explicit_engine:
        candidates.append(explicit_engine)

    env_candidate = DEFAULT_TECTONIC_BIN or os.environ.get("PALIMPSEST_TECTONIC_BIN")
    if env_candidate:
        candidates.append(env_candidate)

    path_candidate = shutil.which("tectonic")
    if path_candidate:
        candidates.append(path_candidate)

    home_bin = Path.home() / "bin" / "tectonic.exe"
    candidates.append(str(home_bin))

    for candidate in candidates:
        path = Path(candidate).expanduser()
        if path.exists():
            return path.resolve()

    raise FileNotFoundError(
        "Could not find a tectonic binary. Set PALIMPSEST_TECTONIC_BIN or put tectonic on PATH."
    )


@dataclass
class RenderedEditionArtifact:
    packet_path: Path
    tex_path: Path
    pdf_path: Path
    meta_path: Path
    engine_path: Path
    stdout_tail: str
    stderr_tail: str


def _normalize_edition_tex(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    updated: list[str] = []

    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("["):
            prefix_len = len(line) - len(stripped)
            line = (" " * prefix_len) + "{}" + stripped
        updated.append(line)

    normalized = "\n".join(updated) + ("\n" if text.endswith("\n") else "")
    if normalized != text:
        path.write_text(normalized, encoding="utf-8")


def render_packet_edition(
    packet_path: Path,
    *,
    engine: str | None = None,
    keep_logs: bool = False,
) -> RenderedEditionArtifact:
    packet = repair_packet_json(Path(packet_path))
    packet_path = Path(packet_path).resolve()
    packet_dir = packet_path.parent
    packet_meta_path = packet_dir / "packet_meta.json"

    edition_tex = Path(packet.files["edition_tex"].path).resolve()
    edition_pdf = Path(packet.files["edition_pdf"].path).resolve()
    meta_path = packet_dir / "edition_render_meta.json"
    engine_path = _find_tectonic(engine)
    font_policy = resolve_edition_font_policy().as_dict()

    sync_packet_edition_template(packet)
    _normalize_edition_tex(edition_tex)

    cmd = [str(engine_path)]
    if keep_logs:
        cmd.append("--keep-logs")
    cmd.append(edition_tex.name)

    proc = subprocess.run(
        cmd,
        cwd=packet_dir,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stdout_tail = proc.stdout[-8000:]
    stderr_tail = proc.stderr[-4000:]

    meta = {
        "rendered_at": _utc_now(),
        "packet_path": str(packet_path),
        "edition_tex_path": str(edition_tex),
        "edition_pdf_path": str(edition_pdf),
        "engine_path": str(engine_path),
        "edition_font_policy": font_policy,
        "command": cmd,
        "returncode": proc.returncode,
        "stdout_tail": stdout_tail,
        "stderr_tail": stderr_tail,
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    if proc.returncode != 0:
        summary = stderr_tail.strip() or stdout_tail.strip() or "Unknown Tectonic failure"
        raise RuntimeError(f"Edition render failed: {summary}")

    packet = repair_packet_json(packet_path)
    packet.files["edition_pdf"].status = "draft" if edition_pdf.exists() else "empty"
    packet.files["edition_pdf"].note = "Compiled PDF rendering of edition_spread.tex" if edition_pdf.exists() else None
    packet_path.write_text(packet.model_dump_json(indent=2), encoding="utf-8")

    if packet_meta_path.exists():
        packet_meta = json.loads(packet_meta_path.read_text(encoding="utf-8"))
    else:
        packet_meta = {
            "generated_at": _utc_now(),
            "source_image_path": packet.source_image_path,
            "packet_path": str(packet_path),
            "prepared_image_path": packet.prepared_image_path,
        }
    packet_meta["edition_font_policy"] = font_policy
    packet_meta_path.write_text(json.dumps(packet_meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return RenderedEditionArtifact(
        packet_path=packet_path,
        tex_path=edition_tex,
        pdf_path=edition_pdf,
        meta_path=meta_path,
        engine_path=engine_path,
        stdout_tail=stdout_tail,
        stderr_tail=stderr_tail,
    )
