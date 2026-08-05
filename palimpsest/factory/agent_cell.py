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
  has logged into omp (``token-plan/...`` works out of the box here; OpenAI
  models need a one-time interactive ``omp`` ``/login``).

The workspace lives under the document's ``runs/`` directory so every crop,
log, and draft stays inspectable after the cell completes.
"""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from palimpsest.factory.config import AGENT_TIMEOUT_SECONDS

DEFAULT_TIMEOUT_S = AGENT_TIMEOUT_SECONDS
EXECUTORS = ("codex", "omp")
_SESSION_RE = re.compile(r"session id: ([0-9a-f-]{36})")
_TOKENS_RE = re.compile(r"tokens used\n([\d,]+)")
_SUBPROCESS_CREATION_FLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_LUNA_MODEL_ID = "gpt-5.6-luna"
_LUNA_THINKING_LEVEL = "xhigh"


class AgentCellError(RuntimeError):
    pass


@dataclass
class AgentRun:
    session_id: str
    tokens: int
    log_path: Path
    cost_usd: float | None = None
    process_stats: dict[str, int] | None = None


def stage_workspace(
    root: Path, skill: str, evidence: dict[str, dict], images: list[Path]
) -> Path:
    """Lay out the cell's airlock: AGENTS.md (the skill), evidence/*.json,
    images/, out/. ``root`` is recreated from scratch — a cell re-run must
    not inherit a previous attempt's state."""
    invalid_evidence = [
        name for name in evidence if not name or Path(name).name != name
    ]
    if invalid_evidence:
        raise AgentCellError(f"invalid evidence names: {invalid_evidence}")
    image_names = [image.name for image in images]
    if len(image_names) != len(set(image_names)):
        raise AgentCellError("staged images must have unique file names")
    missing_images = [str(image) for image in images if not image.is_file()]
    if missing_images:
        raise AgentCellError(f"staged images not found: {missing_images}")

    if root.exists():
        shutil.rmtree(root)
    for sub in ("evidence", "images", "out"):
        (root / sub).mkdir(parents=True)
    (root / "AGENTS.md").write_text(skill, encoding="utf-8")
    for name, payload in evidence.items():
        (root / "evidence" / f"{name}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )
    for image in images:
        shutil.copy(image, root / "images" / image.name)
    return root


def _require_executor(executor: str) -> None:
    if executor not in EXECUTORS:
        raise AgentCellError(
            f"unknown agent executor {executor!r}; expected one of {EXECUTORS}"
        )


def run(
    workspace: Path,
    task: str,
    model: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    executor: str = "codex",
    tool_names: tuple[str, ...] | None = None,
) -> AgentRun:
    _require_executor(executor)
    if executor == "omp":
        args = ["--model", model]
        # Pin Luna's effort in the command instead of inheriting mutable OMP settings.
        if model.rsplit("/", 1)[-1] == _LUNA_MODEL_ID:
            args.extend(("--thinking", _LUNA_THINKING_LEVEL))
        if tool_names is not None:
            if any(not name or "," in name for name in tool_names):
                raise AgentCellError("OMP tool names must be non-empty and comma-free")
            if len(tool_names) != len(set(tool_names)):
                raise AgentCellError("OMP tool names must be unique")
            args.extend(
                ["--tools", ",".join(tool_names)] if tool_names else ["--no-tools"]
            )
        return _omp(workspace, args, task, timeout_s, "agent_run.log")
    if tool_names is not None:
        raise AgentCellError("tool_names is supported only by the OMP executor")
    images = sorted((workspace / "images").glob("*"))
    args = [
        "exec",
        "--disable",
        "plugins",
        "--disable",
        "multi_agent",
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
        "-m",
        model,
        "-s",
        "workspace-write",
        "--skip-git-repo-check",
        "-C",
        str(workspace),
        "-o",
        str(workspace / "out" / "last_message.txt"),
    ]
    for image in images:
        args += ["-i", str(image)]
    return _codex(workspace, args, task, timeout_s, "agent_run.log")


def resume(
    workspace: Path,
    session_id: str,
    message: str,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    executor: str = "codex",
) -> AgentRun:
    _require_executor(executor)
    if executor == "omp":
        return _omp(
            workspace, ["-r", session_id], message, timeout_s, "agent_resume.log"
        )
    args = [
        "exec",
        "--disable",
        "plugins",
        "--disable",
        "multi_agent",
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
        "-s",
        "workspace-write",
        "--skip-git-repo-check",
        "-C",
        str(workspace),
        "-o",
        str(workspace / "out" / "last_message.txt"),
        "resume",
        session_id,
    ]
    return _codex(workspace, args, message, timeout_s, "agent_resume.log")


def _codex(
    workspace: Path, args: list[str], stdin_text: str, timeout_s: int, log_name: str
) -> AgentRun:
    log_path = _run_process("codex", args, workspace, stdin_text, timeout_s, log_name)
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


def _omp(
    workspace: Path, extra_args: list[str], prompt: str, timeout_s: int, log_name: str
) -> AgentRun:
    """omp keeps sessions as JSONL under --session-dir; pinning that inside
    the workspace makes the session id and usage readable in place. Images
    need no attaching — the agent views files from images/ directly."""
    session_dir = workspace / "out" / "sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    # Session files carry cumulative usage; report the delta so run+repair
    # totals add up the same way they do for codex.
    before_files = list(session_dir.rglob("*.jsonl"))
    before_tokens, before_cost = _omp_usage_total(before_files)
    before_stats = _omp_process_stats_total(before_files)
    args = [
        "-p",
        "--no-skills",
        "--cwd",
        str(workspace),
        "--session-dir",
        str(session_dir),
        *extra_args,
        prompt,
    ]
    log_path = _run_process("omp", args, workspace, "", timeout_s, log_name)
    sessions = sorted(session_dir.rglob("*.jsonl"), key=lambda p: p.stat().st_mtime)
    if not sessions:
        raise AgentCellError(f"omp left no session under {session_dir}")
    tokens, cost = _omp_usage_total(sessions)
    after_stats = _omp_process_stats_total(sessions)
    return AgentRun(
        session_id=_omp_session_id(sessions[-1]),
        tokens=tokens - before_tokens,
        log_path=log_path,
        cost_usd=None if cost is None or before_cost is None else cost - before_cost,
        process_stats={
            key: after_stats[key] - before_stats[key] for key in after_stats
        },
    )


def _omp_session_id(session_file: Path) -> str:
    # <timestamp>_<session-id>.jsonl
    return session_file.stem.rsplit("_", 1)[-1]


def _omp_usage_total(session_files: Iterable[Path]) -> tuple[int, float | None]:
    tokens = 0
    cost = 0.0
    cost_known = True
    for session_file in session_files:
        file_tokens, file_cost = _omp_usage(session_file)
        tokens += file_tokens
        if file_cost is None:
            cost_known = False
        else:
            cost += file_cost
    return tokens, cost if cost_known else None


def _omp_usage(session_file: Path) -> tuple[int, float | None]:
    tokens = 0
    cost = 0.0
    observed_usage = False
    cost_known = True
    for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        usage = (record.get("message") or {}).get("usage") or record.get("usage")
        if not usage:
            continue
        observed_usage = True
        tokens += int(usage.get("input", usage.get("input_tokens", 0)) or 0)
        tokens += int(usage.get("output", usage.get("output_tokens", 0)) or 0)
        value = (usage.get("cost") or {}).get("total")
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
            or value < 0
        ):
            cost_known = False
        else:
            cost += float(value)
    return tokens, cost if observed_usage and cost_known else None


def _omp_process_stats_total(session_files: Iterable[Path]) -> dict[str, int]:
    totals = {"assistant_turns": 0, "tool_calls": 0, "output_tokens": 0}
    for session_file in session_files:
        stats = _omp_process_stats(session_file)
        for key in totals:
            totals[key] += stats[key]
    return totals


def _omp_process_stats(session_file: Path) -> dict[str, int]:
    """Process shape of one session: how the agent behaved, not what it cost.

    Counts assistant messages, their toolCall content blocks, and their
    output tokens so a downstream evaluator can tell an agent that gave up
    early from one that thrashed. Tolerates malformed lines the same way
    ``_omp_usage`` does.
    """
    assistant_turns = 0
    tool_calls = 0
    output_tokens = 0
    for line in session_file.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        message = record.get("message")
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        assistant_turns += 1
        content = message.get("content")
        if isinstance(content, list):
            tool_calls += sum(
                1
                for block in content
                if isinstance(block, dict) and block.get("type") == "toolCall"
            )
        usage = message.get("usage")
        if isinstance(usage, dict):
            output_tokens += int(
                usage.get("output", usage.get("output_tokens", 0)) or 0
            )
    return {
        "assistant_turns": assistant_turns,
        "tool_calls": tool_calls,
        "output_tokens": output_tokens,
    }


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """Hard-kill the agent and every descendant, then reap the direct child.

    On Windows ``proc.kill()`` terminates only the direct child; a hung
    omp -> bun grandchild would keep running past the budget, so the tree
    must go down via ``taskkill /T`` while the parent is still alive. On
    POSIX the child leads its own session (``start_new_session``), so the
    whole process group takes one SIGKILL.
    """
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            creationflags=_SUBPROCESS_CREATION_FLAGS,
        )
    else:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except OSError:
            pass
    try:
        proc.kill()
    except OSError:
        pass
    if proc.stdin is not None:
        try:
            proc.stdin.close()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _run_process(
    binary: str,
    args: list[str],
    workspace: Path,
    stdin_text: str,
    timeout_s: int,
    log_name: str,
) -> Path:
    exe = shutil.which(binary)
    if not exe:
        raise AgentCellError(f"{binary} CLI not found on PATH")
    log_path = workspace / "out" / log_name
    command = [exe, *args]
    with log_path.open("w", encoding="utf-8", errors="replace") as log:
        proc = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=log,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=_SUBPROCESS_CREATION_FLAGS,
            start_new_session=os.name != "nt",
        )
        try:
            proc.communicate(input=stdin_text, timeout=timeout_s)
        except subprocess.TimeoutExpired as error:
            _kill_process_tree(proc)
            raise AgentCellError(
                f"agent exceeded {timeout_s}s — log: {log_path}"
            ) from error
    if proc.returncode != 0:
        raise AgentCellError(
            f"{binary} exited {proc.returncode} — log: {log_path}"
        ) from subprocess.CalledProcessError(proc.returncode, command)
    return log_path


def read_artifact(workspace: Path, name: str) -> dict:
    path = workspace / "out" / name
    if not path.exists():
        raise AgentCellError(f"agent did not produce {name} — see {workspace}")
    try:
        # utf-8-sig: agents on Windows often write through PowerShell, which
        # prepends a BOM; accept both
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as error:
        raise AgentCellError(f"agent wrote invalid JSON to {name}: {error}") from error
    if not isinstance(payload, dict):
        raise AgentCellError(f"agent artifact {name} must be a JSON object")
    return payload
