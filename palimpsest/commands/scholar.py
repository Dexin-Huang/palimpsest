from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from palimpsest.config import DEFAULT_MODEL_SCHOLAR_AGENT
from palimpsest.packet_scholar import TASK_CHOICES, build_packet_scholar_prompt, prepare_packet_workspace, run_packet_scholar


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


def _serialize_result(result) -> dict[str, Any]:
    return {
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


def cmd_packet(args: argparse.Namespace) -> None:
    packet_path = Path(args.packet).resolve()
    witness_path = Path(args.witness).resolve() if args.witness else None
    context_paths = [Path(item).resolve() for item in (args.context or [])]

    if args.dry_run:
        packet_inputs = prepare_packet_workspace(
            packet_path,
            witness_path=witness_path,
            context_paths=context_paths,
        )
        prompt = build_packet_scholar_prompt(packet_inputs, task=args.task)
        _write_stdout(prompt)
        return

    result = asyncio.run(
        run_packet_scholar(
            packet_path=packet_path,
            model=args.model,
            task=args.task,
            witness_path=witness_path,
            context_paths=context_paths,
            max_turns=args.max_turns,
            max_budget_usd=args.max_budget_usd,
            max_thinking_tokens=args.max_thinking_tokens,
            permission_mode=args.permission_mode,
        )
    )

    if args.json:
        _write_stdout(json.dumps(_serialize_result(result), indent=2, ensure_ascii=False))
    elif result.response_text:
        _write_stdout(result.response_text)
    elif result.result_text:
        _write_stdout(result.result_text.strip())

    if result.is_error:
        raise SystemExit(1)


def add_subparser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("scholar", help="Dedicated scholar-agent workflow commands")
    sub = parser.add_subparsers(dest="scholar_cmd", required=True)

    packet = sub.add_parser("packet", help="Advance one page.packet with the dedicated Claude scholar agent")
    packet.add_argument("--packet", required=True, help="Path to packet.json")
    packet.add_argument(
        "--task",
        default="advance",
        choices=TASK_CHOICES,
        help="Scholarly packet task to perform (default: advance)",
    )
    packet.add_argument("--witness", help="Optional page witness markdown to ingest into the packet workspace")
    packet.add_argument(
        "--context",
        action="append",
        help="Optional context markdown file; repeat for multiple inputs",
    )
    packet.add_argument(
        "--model",
        default=DEFAULT_MODEL_SCHOLAR_AGENT,
        help=f"Claude model for the scholar agent (default: {DEFAULT_MODEL_SCHOLAR_AGENT})",
    )
    packet.add_argument("--max-turns", type=int, default=80, help="Maximum turns for the scholar run")
    packet.add_argument("--max-budget-usd", type=float, default=None, help="Maximum budget in USD")
    packet.add_argument("--max-thinking-tokens", type=int, default=None, help="Maximum thinking tokens per response")
    packet.add_argument(
        "--permission-mode",
        default="default",
        choices=["default", "plan"],
        help="Claude Code permission mode (default: default)",
    )
    packet.add_argument("--json", action="store_true", help="Emit the full result payload as JSON")
    packet.add_argument("--dry-run", action="store_true", help="Print the generated scholar prompt instead of running Claude")
    packet.set_defaults(func=cmd_packet)
