"""Agent subprocess launch contracts."""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from palimpsest.factory import agent_cell


def test_agent_process_suppresses_windows_console(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir(parents=True)
    observed: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object) -> SimpleNamespace:
        observed["command"] = command
        observed["creationflags"] = kwargs["creationflags"]
        return SimpleNamespace(
            returncode=0,
            pid=4242,
            stdin=None,
            communicate=lambda input=None, timeout=None: (None, None),
        )

    monkeypatch.setattr(agent_cell.shutil, "which", lambda _binary: "omp-test")
    monkeypatch.setattr(agent_cell.subprocess, "Popen", fake_popen)

    log_path = agent_cell._run_process(
        "omp",
        ["--version"],
        workspace,
        "",
        1,
        "agent.log",
    )

    assert observed["command"] == ["omp-test", "--version"]
    assert observed["creationflags"] == agent_cell._SUBPROCESS_CREATION_FLAGS
    assert log_path == workspace / "out" / "agent.log"


def test_run_process_timeout_kills_whole_process_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir(parents=True)
    heartbeat = tmp_path / "heartbeat.txt"
    child_script = textwrap.dedent(
        """
        import subprocess
        import sys
        import time

        grandchild = (
            "import sys, time\\n"
            "while True:\\n"
            "    with open(sys.argv[1], 'a', encoding='utf-8') as fh:\\n"
            "        fh.write('beat\\\\n')\\n"
            "    time.sleep(0.1)\\n"
        )
        subprocess.Popen([sys.executable, "-c", grandchild, sys.argv[1]])
        time.sleep(60)
        """
    )

    with pytest.raises(
        agent_cell.AgentCellError, match="agent exceeded 2s"
    ) as excinfo:
        agent_cell._run_process(
            sys.executable,
            ["-c", child_script, str(heartbeat)],
            workspace,
            "",
            2,
            "agent.log",
        )
    assert isinstance(excinfo.value.__cause__, subprocess.TimeoutExpired)

    # The grandchild proves its death by silence: it heartbeats every 0.1s,
    # so a stable file size across 1.5s means the whole tree went down.
    deadline = time.monotonic() + 5.0
    while True:
        size_before = heartbeat.stat().st_size if heartbeat.exists() else -1
        time.sleep(1.5)
        size_after = heartbeat.stat().st_size if heartbeat.exists() else -1
        if size_before >= 0 and size_before == size_after:
            break
        assert time.monotonic() < deadline, "grandchild survived the tree kill"


@pytest.mark.parametrize(
    ("model", "thinking_level"),
    [
        ("openai-codex/gpt-5.6-luna", "xhigh"),
        ("openai-codex/gpt-5.6-sol", None),
    ],
)
def test_omp_agent_disables_unrelated_global_skills(
    tmp_path: Path,
    monkeypatch,
    model: str,
    thinking_level: str | None,
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir(parents=True)
    observed: dict[str, object] = {}

    def fake_process(
        binary: str,
        args: list[str],
        process_workspace: Path,
        stdin_text: str,
        timeout_s: int,
        log_name: str,
    ) -> Path:
        observed["binary"] = binary
        observed["args"] = args
        session_dir = workspace / "out" / "sessions"
        session_file = (
            session_dir / "2026-07-26T00-00-00_00000000-0000-0000-0000-000000000000.jsonl"
        )
        session_file.write_text(
            '{"message":{"usage":{"input":1,"output":2,"cost":{"total":0.125}}}}\n',
            encoding="utf-8",
        )
        log_path = workspace / "out" / log_name
        log_path.write_text("", encoding="utf-8")
        return log_path

    monkeypatch.setattr(agent_cell, "_run_process", fake_process)

    result = agent_cell.run(
        workspace,
        "Produce the station artifact.",
        model,
        executor="omp",
        tool_names=("read",),
    )

    assert observed["binary"] == "omp"
    args = observed["args"]
    assert isinstance(args, list)
    assert "--no-skills" in args
    assert "--no-rules" not in args
    if thinking_level is None:
        assert "--thinking" not in args
    else:
        thinking_index = args.index("--thinking")
        assert args[thinking_index + 1] == thinking_level
    tool_index = args.index("--tools")
    assert args[tool_index + 1] == "read"
    assert result.session_id == "00000000-0000-0000-0000-000000000000"
    assert result.cost_usd == 0.125


def test_codex_agent_disables_unrelated_user_plugins(
    tmp_path: Path, monkeypatch
) -> None:
    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir(parents=True)
    observed: dict[str, object] = {}

    def fake_process(
        binary: str,
        args: list[str],
        process_workspace: Path,
        stdin_text: str,
        timeout_s: int,
        log_name: str,
    ) -> Path:
        observed["binary"] = binary
        observed["args"] = args
        log_path = workspace / "out" / log_name
        log_path.write_text(
            "session id: 00000000-0000-0000-0000-000000000000\n",
            encoding="utf-8",
        )
        return log_path

    monkeypatch.setattr(agent_cell, "_run_process", fake_process)

    result = agent_cell.run(
        workspace,
        "Produce the station artifact.",
        "gpt-5.6-sol",
        executor="codex",
    )

    assert observed["binary"] == "codex"
    args = observed["args"]
    assert isinstance(args, list)
    disable_index = args.index("--disable")
    assert args[disable_index + 1] == "plugins"
    assert args[disable_index + 2 : disable_index + 8] == [
        "--disable",
        "multi_agent",
        "--disable",
        "shell_tool",
        "--disable",
        "unified_exec",
    ]
    assert result.session_id == "00000000-0000-0000-0000-000000000000"


def test_omp_process_stats_counts_turns_tool_calls_and_output_tokens(
    tmp_path: Path,
) -> None:
    session_file = tmp_path / "session.jsonl"
    session_file.write_text(
        "\n".join(
            [
                '{"type":"session","version":3}',
                "this line is not json",
                "[1, 2, 3]",
                '{"message":{"role":"user","content":[{"type":"text","text":"go"}]}}',
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "content": [
                                {"type": "thinking", "thinking": "hm"},
                                {"type": "toolCall", "id": "call_1", "name": "read"},
                                {"type": "toolCall", "id": "call_2", "name": "grep"},
                            ],
                            "usage": {"input": 10, "output": 7},
                        }
                    }
                ),
                '{"message":{"role":"toolResult","content":[{"type":"text","text":"ok"}]}}',
                json.dumps(
                    {
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "done"}],
                            "usage": {"input": 12, "output": 5},
                        }
                    }
                ),
                '{"message":{"role":"assistant","content":"bare string"}}',
            ]
        ),
        encoding="utf-8",
    )

    assert agent_cell._omp_process_stats(session_file) == {
        "assistant_turns": 3,
        "tool_calls": 2,
        "output_tokens": 12,
    }


def test_omp_process_stats_total_sums_all_session_files(tmp_path: Path) -> None:
    first = tmp_path / "a.jsonl"
    first.write_text(
        '{"message":{"role":"assistant","content":[{"type":"toolCall","id":"c"}],'
        '"usage":{"output":3}}}\n',
        encoding="utf-8",
    )
    second = tmp_path / "b.jsonl"
    second.write_text(
        '{"message":{"role":"assistant","content":[{"type":"text","text":"x"}],'
        '"usage":{"output_tokens":4}}}\n',
        encoding="utf-8",
    )

    assert agent_cell._omp_process_stats_total([first, second]) == {
        "assistant_turns": 2,
        "tool_calls": 1,
        "output_tokens": 7,
    }
    assert agent_cell._omp_process_stats_total([]) == {
        "assistant_turns": 0,
        "tool_calls": 0,
        "output_tokens": 0,
    }
