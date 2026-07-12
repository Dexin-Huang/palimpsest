"""agent_cell: run an agentic editor inside a station's contract.

The station stays a hermetic cell — declared inputs staged into a workspace,
one artifact out, usage reported. Inside, the executor is a coding-agent
harness instead of a single model call: the agent reads the staged evidence,
zooms into page images by cropping and viewing them, and writes the finished
artifact, guided by the skill the station passes as AGENTS.md. ``resume``
sends a follow-up turn into the same session — the verifier's rejection
report goes back to the agent that made the mistake.

Two executors, selected per recipe slot (``executor: codex | omp``):
- ``codex`` — the Codex CLI; models resolve against the user's OpenAI plan.
- ``omp`` — oh-my-pi; models resolve against whatever providers the user
  has logged into omp (``google/...`` works out of the box here; OpenAI
  models need a one-time interactive ``omp`` ``/login``).

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
EXECUTORS = ("codex", "omp")
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
        timeout_s: int = DEFAULT_TIMEOUT_S, executor: str = "codex") -> AgentRun:
    if executor == "omp":
        return _omp(workspace, ["--model", model], task, timeout_s,
                    "agent_run.log")
    images = sorted((workspace / "images").glob("*"))
    args = ["exec", "-m", model, "-s", "workspace-write",
            "--skip-git-repo-check", "-C", str(workspace),
            "-o", str(workspace / "out" / "last_message.txt")]
    for image in images:
        args += ["-i", str(image)]
    return _codex(workspace, args, task, timeout_s, "agent_run.log")


def resume(workspace: Path, session_id: str, message: str,
           timeout_s: int = DEFAULT_TIMEOUT_S, executor: str = "codex") -> AgentRun:
    if executor == "omp":
        return _omp(workspace, ["-r", session_id], message, timeout_s,
                    "agent_resume.log")
    args = ["exec", "-s", "workspace-write", "--skip-git-repo-check",
            "-C", str(workspace),
            "-o", str(workspace / "out" / "last_message.txt"),
            "resume", session_id]
    return _codex(workspace, args, message, timeout_s, "agent_resume.log")


def _codex(workspace: Path, args: list[str], stdin_text: str,
           timeout_s: int, log_name: str) -> AgentRun:
    log_path = _run_process("codex", args, workspace, stdin_text,
                            timeout_s, log_name)
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


def _omp(workspace: Path, extra_args: list[str], prompt: str,
         timeout_s: int, log_name: str) -> AgentRun:
    """omp keeps sessions as JSONL under --session-dir; pinning that inside
    the workspace makes the session id and usage readable in place. Images
    need no attaching — the agent views files from images/ directly."""
    session_dir = workspace / "out" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    # session files carry cumulative usage; report the delta so run+repair
    # totals add up the same way they do for codex
    before = sum(_omp_tokens(f) for f in session_dir.rglob("*.jsonl"))
    args = ["-p", "--cwd", str(workspace),
            "--session-dir", str(session_dir), *extra_args, prompt]
    log_path = _run_process("omp", args, workspace, "", timeout_s, log_name)
    sessions = sorted(session_dir.rglob("*.jsonl"),
                      key=lambda p: p.stat().st_mtime)
    if not sessions:
        raise AgentCellError(f"omp left no session under {session_dir}")
    return AgentRun(
        session_id=_omp_session_id(sessions[-1]),
        tokens=sum(_omp_tokens(f) for f in sessions) - before,
        log_path=log_path,
    )


def _omp_session_id(session_file: Path) -> str:
    # <timestamp>_<session-id>.jsonl
    return session_file.stem.rsplit("_", 1)[-1]


def _omp_tokens(session_file: Path) -> int:
    total = 0
    for line in session_file.read_text(encoding="utf-8",
                                       errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = (record.get("message") or {}).get("usage") or record.get("usage")
        if usage:
            total += int(usage.get("input", usage.get("input_tokens", 0)) or 0)
            total += int(usage.get("output", usage.get("output_tokens", 0)) or 0)
    return total


def _run_process(binary: str, args: list[str], workspace: Path,
                 stdin_text: str, timeout_s: int, log_name: str) -> Path:
    exe = shutil.which(binary)
    if not exe:
        raise AgentCellError(f"{binary} CLI not found on PATH")
    log_path = workspace / "out" / log_name
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        try:
            subprocess.run(
                [exe, *args], input=stdin_text, stdout=log,
                stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                errors="replace", timeout=timeout_s, check=True,
            )
        except subprocess.TimeoutExpired as error:
            raise AgentCellError(
                f"agent exceeded {timeout_s}s — log: {log_path}") from error
        except subprocess.CalledProcessError as error:
            raise AgentCellError(
                f"{binary} exited {error.returncode} — log: {log_path}") from error
    return log_path


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
