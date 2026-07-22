"""Contracts for the generic OMP model gateway."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from palimpsest.factory.gateway import (
    GatewayError,
    ImageContent,
    ModelRequest,
    ModelResponse,
)
from palimpsest.factory.gateway import client, gemini, omp


def _assistant_frame(
    *,
    text: str = '{"answer":"ink"}',
    stop_reason: str = "stop",
    provider: str = "openai-codex",
    model: str = "gpt-5.4",
    cost: float = 0.25,
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
                "provider": provider,
                "model": model,
                "usage": {
                    "input": 100,
                    "cacheRead": 20,
                    "cacheWrite": 3,
                    "output": 40,
                    "reasoningTokens": 15,
                    "totalTokens": 163,
                    "cost": {"total": cost},
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


def test_omp_maps_multimodal_structured_request(monkeypatch, tmp_path):
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
        kwargs["stdout"].write(
            (
                _assistant_frame(
                    provider="google",
                    model="gemini-3.6-flash",
                )
                + "\n"
            ).encode("utf-8")
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(omp.subprocess, "run", fake_run)
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }

    response = omp.generate(
        ModelRequest(
            model="google/gemini-3.6-flash",
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
        "google/gemini-3.6-flash",
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
    assert response.model == "google/gemini-3.6-flash"
    assert response.prompt_tokens == 123
    assert response.output_tokens == 25
    assert response.thought_tokens == 15
    assert response.total_tokens == 163
    assert response.billable_output_tokens == 40
    assert observed["stdin"] is subprocess.DEVNULL
    assert response.cost_usd == 0.25


def test_omp_zeroes_reported_cost_for_codex_subscription(monkeypatch):
    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(
        omp.subprocess,
        "run",
        _completed_run(_assistant_frame(cost=9.75)),
    )

    response = omp.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))

    assert response.cost_usd == 0.0


def test_omp_omits_thinking_when_unspecified(tmp_path):
    request = ModelRequest(model="google/gemini-3.6-flash", prompt="Read")

    command = omp._command(
        "omp-test",
        request,
        prompt_path=tmp_path / "prompt.txt",
        system_path=tmp_path / "system.txt",
        image_paths=(),
    )

    assert "--thinking" not in command


def test_gateway_routes_slash_qualified_google_models_through_omp(monkeypatch):
    expected = SimpleNamespace(text="routed")
    monkeypatch.setattr(omp, "generate", lambda request: expected)

    response = client.generate(
        ModelRequest(model="google/gemini-3.6-flash", prompt="Route me")
    )

    assert response is expected


def test_gateway_routes_bare_gemini_models_to_direct_api(monkeypatch):
    expected = SimpleNamespace(text="direct")
    monkeypatch.setattr(gemini, "generate", lambda request: expected)

    response = client.generate(ModelRequest(model="gemini-3.6-flash", prompt="Read"))

    assert response is expected


def test_omp_marks_timeouts_transient(monkeypatch):
    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("omp-test", 900)

    monkeypatch.setattr(omp.subprocess, "run", timeout)

    with pytest.raises(GatewayError) as raised:
        omp.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Wait"))

    assert raised.value.transient is True
    assert raised.value.cost_usd == 0.0


def test_omp_marks_connection_reset_start_failure_transient(monkeypatch):
    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")

    def connection_reset(*args, **kwargs):
        raise OSError("connection reset by peer")

    monkeypatch.setattr(omp.subprocess, "run", connection_reset)

    with pytest.raises(GatewayError, match="Could not start OMP") as raised:
        omp.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))

    assert raised.value.transient is True
    assert raised.value.tokens_in == 0
    assert raised.value.tokens_out == 0
    assert raised.value.cost_usd == 0.0


def test_omp_rejects_missing_cli_and_invalid_media(monkeypatch, tmp_path):
    monkeypatch.setattr(omp.shutil, "which", lambda command: None)
    with pytest.raises(GatewayError, match="OMP command not found"):
        omp.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))

    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")
    image = tmp_path / "folio.tiff"
    image.write_bytes(b"tiff")
    with pytest.raises(GatewayError, match="Unsupported image type"):
        omp.generate(
            ModelRequest(model="openai-codex/gpt-5.4", prompt="Read", images=(image,))
        )


def test_omp_returns_partial_message_and_usage_for_length_stop(monkeypatch):
    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(
        omp.subprocess,
        "run",
        _completed_run(_assistant_frame(text='{"answer":"ink', stop_reason="length")),
    )

    response = omp.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))

    assert response.text == '{"answer":"ink'
    assert response.finish_reason == "LENGTH"
    assert response.prompt_tokens == 123
    assert response.billable_output_tokens == 40
    assert response.cost_usd == 0.0


@pytest.mark.parametrize(
    ("stop_reason", "expected"),
    [
        ("length", "LENGTH"),
        ("max_tokens", "MAX_TOKENS"),
        ("max-token", "MAX_TOKENS"),
        ("incomplete", "INCOMPLETE"),
    ],
)
def test_omp_normalizes_only_truncation_stop_reasons(stop_reason, expected):
    assert omp._normalized_truncation_reason(stop_reason) == expected


def test_omp_rejects_nontruncation_stop_with_usage(monkeypatch):
    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(
        omp.subprocess,
        "run",
        _completed_run(_assistant_frame(stop_reason="content_filter")),
    )

    with pytest.raises(GatewayError, match="stop_reason=content_filter") as raised:
        omp.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))

    assert raised.value.finish_reason is None
    assert raised.value.tokens_in == 123
    assert raised.value.tokens_out == 40
    assert raised.value.cost_usd == 0.0


def test_omp_rejects_malformed_response(monkeypatch):
    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(
        omp.subprocess,
        "run",
        _completed_run("not-json\n"),
    )
    with pytest.raises(GatewayError, match="1 malformed output lines"):
        omp.generate(ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"))


@pytest.mark.parametrize(
    "detail",
    [
        "HTTP 500 internal server error",
        "HTTP 502 bad gateway",
        "HTTP 503 service unavailable",
        "HTTP 504 gateway timeout",
        "The service is overloaded",
        "read: connection reset by peer",
        "socket ECONNRESET",
    ],
)
def test_omp_classifies_retryable_failures(detail):
    assert omp._is_transient(detail) is True


def test_generate_json_retries_schema_invalid_shape_and_aggregates_usage(
    monkeypatch,
):
    responses = iter(
        [
            ModelResponse(
                text='{"answer":7}',
                model="openai-codex/gpt-5.4",
                prompt_tokens=10,
                output_tokens=4,
                total_tokens=15,
                thought_tokens=1,
                cost_usd=0.2,
            ),
            ModelResponse(
                text='{"answer":"ink"}',
                model="openai-codex/gpt-5.4",
                prompt_tokens=20,
                output_tokens=6,
                total_tokens=28,
                thought_tokens=2,
                cost_usd=0.3,
            ),
        ]
    )
    monkeypatch.setattr(client, "generate", lambda request: next(responses))
    request = ModelRequest(
        model="openai-codex/gpt-5.4",
        prompt="Read",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
            "additionalProperties": False,
        },
    )

    value, response = client.generate_json(request, attempts=2)

    assert value == {"answer": "ink"}
    assert response.prompt_tokens == 30
    assert response.output_tokens == 10
    assert response.thought_tokens == 3
    assert response.total_tokens == 43
    assert response.cost_usd == pytest.approx(0.5)


def test_generate_json_schema_failure_reports_all_attempt_usage(monkeypatch):
    responses = iter(
        [
            ModelResponse(
                text="1",
                model="openai-codex/gpt-5.4",
                prompt_tokens=2,
                output_tokens=3,
                total_tokens=5,
                cost_usd=0.1,
            ),
            ModelResponse(
                text="2",
                model="openai-codex/gpt-5.4",
                prompt_tokens=5,
                output_tokens=7,
                total_tokens=12,
                cost_usd=0.2,
            ),
        ]
    )
    monkeypatch.setattr(client, "generate", lambda request: next(responses))

    with pytest.raises(GatewayError, match="violates the requested schema") as raised:
        client.generate_json(
            ModelRequest(
                model="openai-codex/gpt-5.4",
                prompt="Read",
                json_schema={"type": "string"},
            ),
            attempts=2,
        )

    assert raised.value.tokens_in == 7
    assert raised.value.tokens_out == 10
    assert raised.value.cost_usd == pytest.approx(0.3)


def test_generate_json_truncated_invalid_json_raises_immediately_with_usage(
    monkeypatch,
):
    calls = 0

    def truncated(request):
        nonlocal calls
        calls += 1
        return ModelResponse(
            text='{"answer":',
            model=request.model,
            finish_reason="MAX_TOKENS",
            prompt_tokens=11,
            output_tokens=5,
            total_tokens=18,
            thought_tokens=2,
            cost_usd=0.7,
        )

    monkeypatch.setattr(client, "generate", truncated)

    with pytest.raises(GatewayError, match="truncated unparseable JSON") as raised:
        client.generate_json(
            ModelRequest(model="openai-codex/gpt-5.4", prompt="Read"),
            attempts=3,
        )

    assert calls == 1
    assert raised.value.finish_reason == "MAX_TOKENS"
    assert raised.value.tokens_in == 11
    assert raised.value.tokens_out == 7
    assert raised.value.cost_usd == pytest.approx(0.7)


def test_real_omp_to_generate_json_boundary_carries_truncation(monkeypatch):
    monkeypatch.setattr(omp.shutil, "which", lambda command: "omp-test")
    monkeypatch.setattr(
        omp.subprocess,
        "run",
        _completed_run(
            _assistant_frame(
                text='{"answer":',
                stop_reason="length",
                provider="google",
                model="gemini-3.6-flash",
            )
        ),
    )

    with pytest.raises(GatewayError, match="truncated unparseable JSON") as raised:
        client.generate_json(
            ModelRequest(model="google/gemini-3.6-flash", prompt="Read"),
            attempts=3,
        )

    assert raised.value.finish_reason == "LENGTH"
    assert raised.value.tokens_in == 123
    assert raised.value.tokens_out == 40
    assert raised.value.cost_usd == pytest.approx(0.25)


def test_generate_json_truncated_schema_violation_preserves_signal(monkeypatch):
    monkeypatch.setattr(
        client,
        "generate",
        lambda request: ModelResponse(
            text='{"answer":7}',
            model=request.model,
            finish_reason="INCOMPLETE",
            prompt_tokens=13,
            output_tokens=4,
            total_tokens=17,
            cost_usd=0.4,
        ),
    )
    request = ModelRequest(
        model="openai-codex/gpt-5.4",
        prompt="Read",
        json_schema={
            "type": "object",
            "properties": {"answer": {"type": "string"}},
            "required": ["answer"],
        },
    )

    with pytest.raises(GatewayError, match="violates the requested schema") as raised:
        client.generate_json(request)

    assert raised.value.finish_reason == "INCOMPLETE"
    assert raised.value.tokens_in == 13
    assert raised.value.tokens_out == 4
    assert raised.value.cost_usd == pytest.approx(0.4)


def test_generate_json_rejects_invalid_schema_before_calling_provider(monkeypatch):
    def unexpected_call(request):
        raise AssertionError("provider must not be called for an invalid schema")

    monkeypatch.setattr(client, "generate", unexpected_call)
    request = ModelRequest(
        model="openai-codex/gpt-5.4",
        prompt="Read",
        json_schema={"type": "not-a-json-schema-type"},
    )

    with pytest.raises(GatewayError, match="Invalid JSON schema"):
        client.generate_json(request)


def test_gateway_error_usage_wrapping_preserves_finish_reason():
    error = GatewayError(
        "truncated",
        tokens_in=3,
        tokens_out=4,
        cost_usd=0.5,
        finish_reason="LENGTH",
    )

    wrapped = error.with_prior_usage(tokens_in=10, tokens_out=20, cost_usd=0.25)

    assert wrapped.finish_reason == "LENGTH"
    assert wrapped.tokens_in == 13
    assert wrapped.tokens_out == 24
    assert wrapped.cost_usd == pytest.approx(0.75)
