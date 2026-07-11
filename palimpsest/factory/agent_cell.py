"""agent_cell: run an agentic editor inside a station's contract.

The station stays a hermetic cell — declared inputs staged into a workspace,
one artifact out, usage reported. Inside, the executor is a coding-agent
harness (Codex CLI) instead of a single model call: the agent reads the
staged evidence, zooms into page images by cropping and viewing them, and
writes the finished artifact, guided by the skill the station passes as
AGENTS.md. ``resume`` sends one follow-up turn into the same session — the
verifier's rejection report goes back to the agent that made the mistake.

The workspace lives under the document's ``runs/`` directory so every crop,
log, and draft stays inspectable after the cell completes.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_TIMEOUT_S = 2400
_SESSION_RE = re.compile(r"session id: ([0-9a-f-]{36})")
_TOKENS_RE = re.compile(r"tokens used\n([\d,]+)")


class AgentCellError(RuntimeError):
    pass


@dataclass
class AgentRun:
    session_id: str
    tokens: int
    log_path: Path


def stage_workspace(root: Path, skill: str, evidence: dict[str, dict],
                    images: list[Path]) -> Path:
    """Lay out the cell's airlock: AGENTS.md (the skill), evidence/*.json,
    images/, out/. ``root`` is recreated from scratch — a cell re-run must
    not inherit a previous attempt's state."""
    if root.exists():
        shutil.rmtree(root)
    for sub in ("evidence", "images", "out"):
        (root / sub).mkdir(parents=True)
    (root / "AGENTS.md").write_text(skill, encoding="utf-8")
    for name, payload in evidence.items():
        (root / "evidence" / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    for image in images:
        shutil.copy(image, root / "images" / image.name)
    return root


def run(workspace: Path, task: str, model: str,
        timeout_s: int = DEFAULT_TIMEOUT_S) -> AgentRun:
    images = sorted((workspace / "images").glob("*"))
    args = ["exec", "-m", model, "-s", "workspace-write",
            "--skip-git-repo-check", "-C", str(workspace),
            "-o", str(workspace / "out" / "last_message.txt")]
    for image in images:
        args += ["-i", str(image)]
    return _invoke(workspace, args, task, timeout_s, "agent_run.log")


def resume(workspace: Path, session_id: str, message: str,
           timeout_s: int = DEFAULT_TIMEOUT_S) -> AgentRun:
    args = ["exec", "-s", "workspace-write", "--skip-git-repo-check",
            "-C", str(workspace),
            "-o", str(workspace / "out" / "last_message.txt"),
            "resume", session_id]
    return _invoke(workspace, args, message, timeout_s, "agent_resume.log")


def _invoke(workspace: Path, args: list[str], stdin_text: str,
            timeout_s: int, log_name: str) -> AgentRun:
    codex = shutil.which("codex")
    if not codex:
        raise AgentCellError("codex CLI not found on PATH")
    log_path = workspace / "out" / log_name
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        try:
            subprocess.run(
                [codex, *args], input=stdin_text, stdout=log,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_s, check=True,
            )
        except subprocess.TimeoutExpired as error:
            raise AgentCellError(
                f"agent exceeded {timeout_s}s — log: {log_path}") from error
        except subprocess.CalledProcessError as error:
            raise AgentCellError(
                f"codex exited {error.returncode} — log: {log_path}") from error

    text = log_path.read_text(encoding="utf-8", errors="replace")
    session = _SESSION_RE.search(text)
    tokens = _TOKENS_RE.search(text)
    if not session:
        raise AgentCellError(f"no session id in agent log: {log_path}")
    return AgentRun(
        session_id=session.group(1),
        tokens=int(tokens.group(1).replace(",", "")) if tokens else 0,
        log_path=log_path,
    )


def read_artifact(workspace: Path, name: str) -> dict:
    path = workspace / "out" / name
    if not path.exists():
        raise AgentCellError(f"agent did not produce {name} — see {workspace}")
    try:
        # utf-8-sig: agents on Windows often write through PowerShell, which
        # prepends a BOM; accept both
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise AgentCellError(f"agent wrote invalid JSON to {name}: {error}") from error
