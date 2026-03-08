from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from palimpsest.config import DEFAULT_MODEL_AGENT, PROJECT_ROOT

PROFILE_CHOICES = ["general", "edit", "inspect", "summarize"]


def _coerce_bool(value: Any, *, field_name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise SystemExit(f"Invalid boolean for {field_name}: {value!r}")


def _read_prompt(raw_prompt: str | None) -> str:
    if raw_prompt:
        if raw_prompt.startswith("@"):
            path = Path(raw_prompt[1:]).resolve()
            if not path.exists():
                raise SystemExit(f"Prompt file not found: {path}")
            return path.read_text(encoding="utf-8").strip()
        return raw_prompt.strip()

    if not sys.stdin.isatty():
        data = sys.stdin.read().strip()
        if data:
            return data

    raise SystemExit("Error: provide a prompt argument or pipe one on stdin")


def _write_stdout(text: str) -> None:
    data = text if text.endswith("\n") else f"{text}\n"
    try:
        sys.stdout.write(data)
    except UnicodeEncodeError:
        if hasattr(sys.stdout, "buffer"):
            encoding = sys.stdout.encoding or "utf-8"
            sys.stdout.buffer.write(data.encode(encoding, errors="replace"))
        else:
            sys.stdout.write(data.encode("ascii", errors="replace").decode("ascii"))


def _resolve_workspace(raw_workspace: str) -> Path:
    workspace = Path(raw_workspace).resolve()
    if not workspace.exists():
        raise SystemExit(f"Workspace not found: {workspace}")
    if not workspace.is_dir():
        raise SystemExit(f"Workspace is not a directory: {workspace}")
    return workspace


def _serialize_result(result) -> dict[str, Any]:
    return {
        "job_id": result.job_id,
        "workspace": str(result.workspace),
        "profile": result.profile,
        "response_text": result.response_text,
        "result": result.result_text,
        "structured_output": result.structured_output,
        "session_id": result.session_id,
        "total_cost_usd": result.total_cost_usd,
        "num_turns": result.num_turns,
        "subtype": result.subtype,
        "is_error": result.is_error,
        "messages": result.messages,
    }


def _emit_single_result(result, emit_json: bool) -> None:
    if emit_json:
        _write_stdout(json.dumps(_serialize_result(result), indent=2, ensure_ascii=False))
        return

    if result.response_text:
        _write_stdout(result.response_text)
    elif result.result_text:
        _write_stdout(result.result_text.strip())


def _load_job_specs(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8-sig").strip()
    if not raw:
        return []

    if raw.startswith("["):
        payload = json.loads(raw)
        if not isinstance(payload, list):
            raise SystemExit("Batch input JSON must be a list of job objects")
        return payload

    jobs: list[dict[str, Any]] = []
    for lineno, line in enumerate(raw.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            item = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Invalid JSON on line {lineno} of {path}: {exc}") from exc
        if not isinstance(item, dict):
            raise SystemExit(f"Line {lineno} of {path} is not a JSON object")
        jobs.append(item)
    return jobs


def _build_job(spec: dict[str, Any], args: argparse.Namespace, index: int):
    from palimpsest.agent_sdk import AgentJob

    prompt = spec.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        raise SystemExit(f"Batch job {index} is missing a non-empty 'prompt'")

    workspace_value = spec.get("workspace", args.workspace)
    if not isinstance(workspace_value, str):
        raise SystemExit(f"Batch job {index} has non-string 'workspace'")

    profile = spec.get("profile", args.profile)
    if not isinstance(profile, str) or profile not in PROFILE_CHOICES:
        raise SystemExit(
            f"Batch job {index} has invalid profile {profile!r}; expected one of {', '.join(PROFILE_CHOICES)}"
        )

    permission_mode = spec.get("permission_mode", args.permission_mode)
    if not isinstance(permission_mode, str) or permission_mode not in ("default", "plan"):
        raise SystemExit(
            f"Batch job {index} has invalid permission_mode {permission_mode!r}; expected 'default' or 'plan'"
        )

    model = spec.get("model", args.model)
    if not isinstance(model, str) or not model.strip():
        raise SystemExit(f"Batch job {index} has invalid model")

    with_web_search = _coerce_bool(
        spec.get("with_web_search", args.with_web_search),
        field_name=f"batch job {index} with_web_search",
    )

    job_id = spec.get("id")
    if job_id is not None and not isinstance(job_id, str):
        raise SystemExit(f"Batch job {index} has non-string 'id'")

    return AgentJob(
        prompt=prompt.strip(),
        workspace=_resolve_workspace(workspace_value),
        profile=profile,
        with_web_search=with_web_search,
        model=model.strip(),
        max_turns=int(spec.get("max_turns", args.max_turns)),
        max_budget_usd=spec.get("max_budget_usd", args.max_budget_usd),
        max_thinking_tokens=spec.get("max_thinking_tokens", args.max_thinking_tokens),
        permission_mode=permission_mode,
        job_id=job_id or f"job-{index}",
    )


def _run_single(args: argparse.Namespace, profile: str) -> int:
    from palimpsest.agent_sdk import run_agent_prompt

    prompt = _read_prompt(args.prompt)
    workspace = _resolve_workspace(args.workspace)
    result = asyncio.run(
        run_agent_prompt(
            prompt=prompt,
            workspace=workspace,
            model=args.model,
            profile=profile,
            with_web_search=args.with_web_search,
            max_turns=args.max_turns,
            max_budget_usd=args.max_budget_usd,
            max_thinking_tokens=args.max_thinking_tokens,
            permission_mode=args.permission_mode,
        )
    )
    _emit_single_result(result, args.json)
    return 1 if result.is_error else 0


def cmd_agent(args: argparse.Namespace) -> None:
    raise SystemExit(_run_single(args, "general"))


def cmd_agent_edit(args: argparse.Namespace) -> None:
    raise SystemExit(_run_single(args, "edit"))


def cmd_agent_inspect(args: argparse.Namespace) -> None:
    raise SystemExit(_run_single(args, "inspect"))


def cmd_agent_summarize(args: argparse.Namespace) -> None:
    raise SystemExit(_run_single(args, "summarize"))


def cmd_agent_batch(args: argparse.Namespace) -> None:
    from palimpsest.agent_sdk import run_agent_jobs

    input_path = Path(args.input).resolve()
    if not input_path.exists():
        raise SystemExit(f"Batch input not found: {input_path}")

    specs = _load_job_specs(input_path)
    if not specs:
        raise SystemExit(f"Batch input contains no jobs: {input_path}")

    jobs = [_build_job(spec, args, index + 1) for index, spec in enumerate(specs)]
    results = asyncio.run(run_agent_jobs(jobs, concurrency=args.concurrency))
    payload = [_serialize_result(result) for result in results]

    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    elif args.json:
        _write_stdout(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        for result in results:
            header = f"[{result.job_id}] profile={result.profile} workspace={result.workspace}"
            _write_stdout(header)
            body = result.response_text or (result.result_text.strip() if result.result_text else "")
            if body:
                _write_stdout(body)

    if any(result.is_error for result in results):
        raise SystemExit(1)


def _add_common_single_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("prompt", nargs="?", help="Prompt text or @path/to/prompt.txt")
    parser.add_argument(
        "--workspace",
        default=str(PROJECT_ROOT),
        help=f"Workspace root for file operations (default: {PROJECT_ROOT})",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_AGENT,
        help=f"Claude model for the helper agent (default: {DEFAULT_MODEL_AGENT})",
    )
    parser.add_argument("--max-turns", type=int, default=100, help="Maximum turns for the run")
    parser.add_argument("--max-budget-usd", type=float, default=None, help="Maximum budget in USD")
    parser.add_argument(
        "--max-thinking-tokens",
        type=int,
        default=None,
        help="Maximum thinking tokens per response",
    )
    parser.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "plan"],
        help="Claude Code permission mode (default: default)",
    )
    parser.add_argument(
        "--with-web-search",
        action="store_true",
        help="Allow the WebSearch tool for this worker run",
    )
    parser.add_argument("--json", action="store_true", help="Emit the full result payload as JSON")


def add_subparsers(subparsers: argparse._SubParsersAction) -> None:
    general = subparsers.add_parser("agent", help="Run the general Palimpsest Claude helper")
    _add_common_single_args(general)
    general.set_defaults(func=cmd_agent)

    edit = subparsers.add_parser("agent-edit", help="Run the editing worker")
    _add_common_single_args(edit)
    edit.set_defaults(func=cmd_agent_edit)

    inspect = subparsers.add_parser("agent-inspect", help="Run the read-only inspection worker")
    _add_common_single_args(inspect)
    inspect.set_defaults(func=cmd_agent_inspect)

    summarize = subparsers.add_parser("agent-summarize", help="Run the read-only summarization worker")
    _add_common_single_args(summarize)
    summarize.set_defaults(func=cmd_agent_summarize)

    batch = subparsers.add_parser("agent-batch", help="Run multiple agent jobs concurrently")
    batch.add_argument("--input", required=True, help="Path to a JSON or JSONL batch job file")
    batch.add_argument(
        "--workspace",
        default=str(PROJECT_ROOT),
        help=f"Default workspace for jobs that omit one (default: {PROJECT_ROOT})",
    )
    batch.add_argument(
        "--profile",
        default="general",
        choices=PROFILE_CHOICES,
        help="Default profile for jobs that omit one (default: general)",
    )
    batch.add_argument(
        "--model",
        default=DEFAULT_MODEL_AGENT,
        help=f"Default Claude model for jobs that omit one (default: {DEFAULT_MODEL_AGENT})",
    )
    batch.add_argument("--max-turns", type=int, default=100, help="Default max turns per job")
    batch.add_argument("--max-budget-usd", type=float, default=None, help="Default max budget per job")
    batch.add_argument(
        "--max-thinking-tokens",
        type=int,
        default=None,
        help="Default max thinking tokens per job",
    )
    batch.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "plan"],
        help="Default permission mode for jobs that omit one (default: default)",
    )
    batch.add_argument(
        "--with-web-search",
        action="store_true",
        help="Default WebSearch enablement for jobs that omit with_web_search",
    )
    batch.add_argument("--concurrency", type=int, default=2, help="Maximum concurrent jobs (default: 2)")
    batch.add_argument("--output", help="Optional file path for writing the JSON batch results")
    batch.add_argument("--json", action="store_true", help="Emit batch results as JSON to stdout")
    batch.set_defaults(func=cmd_agent_batch)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Agent commands")
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_subparsers(subparsers)
    args = parser.parse_args(argv)
    args.func(args)
