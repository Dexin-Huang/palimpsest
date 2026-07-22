"""Contracts for the subscription-authenticated OMP Codex gateway."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from palimpsest.factory.gateway import GatewayError, ImageContent, ModelRequest
from palimpsest.factory.gateway import client, omp_codex


def _assistant_frame(
    *,
    text: str = '{"answer":"ink"}',
    stop_reason: str = "stop",
) -> str:
    return json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [
                    {"type": "thinking", "thinking": ""},
                    {"type": "text", "text": text},
                ],
                "provider": "openai-codex",
                "model": "gpt-5.4",
                "usage": {
                    "input": 100,
                    "cacheRead": 20,
                    "cacheWrite": 3,
                    "output": 40,
                    "reasoningTokens": 15,
                    "totalTokens": 163,
                    "cost": {"total": 0.25},
                },
                "stopReason": stop_reason,
            },
        }
    )


def _completed_run(stdout: str, *, stderr: str = "", returncode: int = 0):
    def run(*args, **kwargs):
        kwargs["stdout"].write(stdout.encode("utf-8"))
        kwargs["stderr"].write(stderr.encode("utf-8"))
        return SimpleNamespace(returncode=returncode)

    return run


def test_omp_codex_maps_multimodal_structured_request(monkeypatch, tmp_path):
    source_image = tmp_path / "folio.jpg"
    source_image.write_bytes(b"jpeg")
    observed = {}

    def fake_run(command, **kwargs):
        observed["command"] = command
        observed["cwd"] = kwargs["cwd"]
        observed["stdin"] = kwargs["stdin"]
        prompt_argument = next(
            value
            for value in command
            if value.startswith("@") and value.endswith("prompt.txt")
        )
        system_path = Path(command[command.index("--system-prompt") + 1])
        image_arguments = [
            value
            for value in command
            if value.startswith("@") and not value.endswith("prompt.txt")
        ]
        observed["prompt"] = Path(prompt_argument[1:]).read_text(encoding="utf-8")
        observed["system"] = system_path.read_text(encoding="utf-8")
        observed["images"] = [Path(value[1:]).read_bytes() for value in image_arguments]
        kwargs["stdout"].write((_assistant_frame() + "\n").encode("utf-8"))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(omp_codex.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(omp_codex.subprocess, "run", fake_run)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }

    response = omp_codex.generate(
        ModelRequest(
            model="openai-codex/gpt-5.4",
            prompt="Read the folio.",
            system="Transcribe faithfully.",
            images=(source_image, ImageContent(b"png", mime="image/png")),
            media_resolution="high",
            max_output_tokens=1234,
            json_output=True,
            json_schema=schema,
            thinking_level="medium",
        )
    )

    assert observed["command"][:6] == [
        "omp-test",
        "-p",
        "--mode",
        "json",
        "--model",
        "openai-codex/gpt-5.4",
    ]
    assert "--no-tools" in observed["command"]
    assert observed["command"][observed["command"].index("--thinking") + 1] == "medium"
    assert observed["prompt"] == "Read the folio."
    assert "Transcribe faithfully." in observed["system"]
    assert "within 1234 tokens" in observed["system"]
    assert "Return exactly one valid JSON value" in observed["system"]
    assert (
        json.dumps(schema, sort_keys=True, separators=(",", ":")) in observed["system"]
    )
    assert observed["images"] == [b"jpeg", b"png"]
    assert response.text == '{"answer":"ink"}'
    assert response.model == "openai-codex/gpt-5.4"
    assert response.prompt_tokens == 123
    assert response.output_tokens == 25
    assert response.thought_tokens == 15
    assert response.total_tokens == 163
    assert response.billable_output_tokens == 40
    assert observed["stdin"] is subprocess.DEVNULL
    assert response.cost_usd == 0.0


def test_omp_codex_defaults_to_low_reasoning(tmp_path):
    request = ModelRequest(
        model="openai-codex/gpt-5.6-luna",
        prompt="Read",
    )

    command = omp_codex._command(
        "omp-test",
        request,
        prompt_path=tmp_path / "prompt.txt",
        system_path=tmp_path / "system.txt",
        image_paths=(),
    )

    assert command[command.index("--thinking") + 1] == "low"


def test_gateway_routes_openai_codex_models(monkeypatch):
    expected = SimpleNamespace(text="routed")
    monkeypatch.setattr(omp_codex, "generate", lambda request: expected)

    response = client.generate(
        ModelRequest(model="openai-codex/gpt-5.4", prompt="Route me")
    )

    assert response is expected


def test_omp_codex_marks_timeouts_transient(monkeypatch):
    monkeypatch.setattr(omp_codex.shutil, "which", lambda command: "omp-test")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("omp-test", 900)

    monkeypatch.setattr(omp_codex.subprocess, "run", timeout)

    with pytest.raises(GatewayError) as raised:
        omp_codex.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Wait"))

    assert raised.value.transient is True
    assert raised.value.cost_usd == 0.0


def test_omp_codex_rejects_missing_cli_and_invalid_media(monkeypatch, tmp_path):
    monkeypatch.setattr(omp_codex.shutil, "which", lambda command: None)
    with pytest.raises(GatewayError, match="OMP command not found"):
        omp_codex.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))

    monkeypatch.setattr(omp_codex.shutil, "which", lambda command: "omp-test")
    image = tmp_path / "folio.tiff"
    image.write_bytes(b"tiff")
    with pytest.raises(GatewayError, match="Unsupported image type"):
        omp_codex.generate(
            ModelRequest(model="openai-codex/gpt-5.4", prompt="Read", images=(image,))
        )


def test_omp_codex_rejects_incomplete_or_missing_response(monkeypatch):
    monkeypatch.setattr(omp_codex.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(
        omp_codex.subprocess,
        "run",
        _completed_run(_assistant_frame(stop_reason="length")),
    )
    with pytest.raises(GatewayError, match="stop_reason=length") as raised:
        omp_codex.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))
    assert raised.value.tokens_in == 123
    assert raised.value.tokens_out == 40

    monkeypatch.setattr(
        omp_codex.subprocess,
        "run",
        _completed_run('{"type":"agent_end"}\n'),
    )
    with pytest.raises(GatewayError, match="no complete assistant message"):
        omp_codex.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))
