from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    ClaudeAgentOptions,
    ClaudeSDKClient,
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
)
from claude_agent_sdk.types import ResultMessage, SystemPromptPreset

WEB_SEARCH_TOOL = "WebSearch"
READ_ONLY_TOOLS = ["Read", "Glob", "Grep"]
EDIT_TOOLS = ["Read", "Write", "Edit", "MultiEdit", "Glob", "Grep"]
_PATH_SCOPED_TOOLS = {"Read", "Write", "Edit", "MultiEdit", "Glob", "Grep"}
_PATH_KEYS = (
    "path",
    "paths",
    "file",
    "files",
    "file_path",
    "file_paths",
    "directory",
    "dir",
    "root",
    "cwd",
)


@dataclass(frozen=True)
class AgentProfile:
    name: str
    tools: tuple[str, ...]
    instructions: str


PROFILES: dict[str, AgentProfile] = {
    "candidate_scout": AgentProfile(
        name="candidate_scout",
        tools=tuple(READ_ONLY_TOOLS),
        instructions=(
            "You are the dedicated discovery scout for Palimpsest.\n"
            "Work only from the provided candidate bundle and optional web search.\n"
            "Your job is to identify which candidates best preserve vanished human worlds:\n"
            "stories, ritual, mysticism, medicine, divination, cosmology, travel, and local memory.\n"
            "Distinguish direct catalog evidence from inference.\n"
            "Rank candidates by actual payoff for reading, not by prestige or generic obscurity.\n"
            "Return a concise shortlist with concrete next actions.\n"
            "Do not modify files outside the scout workspace.\n"
        ),
    ),
}


@dataclass
class AgentRunResult:
    workspace: Path
    profile: str
    response_text: str
    result_text: str | None
    structured_output: Any
    session_id: str | None
    total_cost_usd: float | None
    num_turns: int | None
    subtype: str | None
    is_error: bool
    messages: list[dict[str, Any]]
    job_id: str | None = None


def get_profile(name: str) -> AgentProfile:
    try:
        return PROFILES[name]
    except KeyError as exc:
        valid = ", ".join(sorted(PROFILES))
        raise ValueError(f"Unknown agent profile {name!r}. Expected one of: {valid}") from exc


def _build_system_prompt(
    workspace: Path,
    profile: AgentProfile,
    allowed_tools: tuple[str, ...] | None = None,
) -> SystemPromptPreset:
    workspace_str = str(workspace).replace("\\", "/")
    tools = allowed_tools or profile.tools
    tools_list = ", ".join(tools)
    append = (
        f"You are Palimpsest's workspace-scoped {profile.name} worker in: {workspace_str}\n"
        f"Available tools: {tools_list}.\n"
        "Do not attempt to use tools outside that list.\n"
        "Behave like a grunt worker: use the fewest tool calls needed to finish the task.\n"
        "If instructions restrict edits to specific files, treat that as a hard constraint.\n"
        "Use the built-in file tools directly instead of shelling out.\n"
        "Stay within the workspace root for all file operations.\n"
        "Do not use destructive git commands unless explicitly requested.\n"
        f"{profile.instructions}"
    )
    return SystemPromptPreset(type="preset", preset="claude_code", append=append)


def _resolve_path(root: Path, raw: str) -> Path:
    path = Path(raw)
    return (path if path.is_absolute() else root / path).resolve(strict=False)


def _normalize_tool_input(tool_name: str, tool_input: dict[str, Any], root: Path):
    if tool_name not in _PATH_SCOPED_TOOLS:
        return tool_input

    raw = tool_input or {}
    normalized = dict(raw)
    changed = False

    for key in _PATH_KEYS:
        value = raw.get(key)
        if isinstance(value, str):
            resolved = _resolve_path(root, value)
            if not resolved.is_relative_to(root):
                return PermissionResultDeny(
                    message=f"Path '{value}' is outside the workspace root",
                    interrupt=True,
                )
            resolved_str = str(resolved)
            if resolved_str != value:
                normalized[key] = resolved_str
                changed = True
        elif isinstance(value, (list, tuple)):
            resolved_items: list[str] = []
            for item in value:
                if not isinstance(item, str):
                    continue
                resolved = _resolve_path(root, item)
                if not resolved.is_relative_to(root):
                    return PermissionResultDeny(
                        message=f"Path '{item}' is outside the workspace root",
                        interrupt=True,
                    )
                resolved_items.append(str(resolved))
            if resolved_items != list(value):
                normalized[key] = resolved_items
                changed = True

    return normalized if changed else tool_input


def _build_can_use_tool(workspace: Path, allowed_tools: list[str]):
    root = workspace.resolve()
    allowed = set(allowed_tools)

    async def can_use_tool(tool_name: str, tool_input: dict[str, Any], context):
        del context
        if tool_name not in allowed:
            return PermissionResultDeny(
                message=f"Tool '{tool_name}' is not allowed",
                interrupt=True,
            )
        result = _normalize_tool_input(tool_name, tool_input, root)
        if isinstance(result, PermissionResultDeny):
            return result

        updated_input = result if result is not tool_input else None
        return PermissionResultAllow(updated_input=updated_input)

    return can_use_tool


def _build_hooks(workspace: Path, allowed_tools: list[str]) -> dict[str, list[HookMatcher]]:
    root = workspace.resolve()
    allowed = set(allowed_tools)

    async def pre_tool_use(input_data, tool_use_id, context):
        del tool_use_id, context
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {}) or {}

        if tool_name not in allowed:
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Tool '{tool_name}' is not allowed",
                }
            }

        result = _normalize_tool_input(tool_name, tool_input, root)
        if isinstance(result, PermissionResultDeny):
            return {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": result.message,
                }
            }

        hook_output: dict[str, Any] = {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "allow",
            }
        }
        if result is not tool_input:
            hook_output["hookSpecificOutput"]["updatedInput"] = result
        return hook_output

    matcher = "|".join(allowed_tools)
    return {"PreToolUse": [HookMatcher(matcher=matcher, hooks=[pre_tool_use])]}


def _safe(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    if isinstance(value, dict):
        return {str(k): _safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe(item) for item in value]
    if hasattr(value, "__dict__"):
        return {k: _safe(v) for k, v in value.__dict__.items() if not k.startswith("_")}
    return str(value)


def _message_to_dict(message: object) -> dict[str, Any]:
    payload: dict[str, Any] = {"type": type(message).__name__}
    for attr in (
        "content",
        "role",
        "tool_use_result",
        "total_cost_usd",
        "num_turns",
        "duration_ms",
        "uuid",
        "result",
        "structured_output",
        "session_id",
        "subtype",
        "is_error",
    ):
        value = getattr(message, attr, None)
        if value is not None:
            payload[attr] = _safe(value)
    return payload


def _extract_text(message: object) -> str:
    content = getattr(message, "content", None)
    if not content:
        return ""

    chunks: list[str] = []
    for block in content:
        text = getattr(block, "text", None)
        if isinstance(text, str) and text:
            chunks.append(text)
    return "".join(chunks).strip()


async def run_agent_prompt(
    *,
    prompt: str,
    workspace: Path,
    model: str,
    profile: str = "candidate_scout",
    with_web_search: bool = False,
    max_turns: int = 100,
    max_budget_usd: float | None = None,
    max_thinking_tokens: int | None = None,
    permission_mode: str = "default",
    job_id: str | None = None,
) -> AgentRunResult:
    workspace = workspace.resolve()
    agent_profile = get_profile(profile)
    allowed_tools = list(agent_profile.tools)
    if with_web_search and WEB_SEARCH_TOOL not in allowed_tools:
        allowed_tools.append(WEB_SEARCH_TOOL)
    options = ClaudeAgentOptions(
        model=model,
        system_prompt=_build_system_prompt(workspace, agent_profile, tuple(allowed_tools)),
        tools=allowed_tools,
        allowed_tools=allowed_tools,
        mcp_servers={},
        permission_mode=permission_mode,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        max_thinking_tokens=max_thinking_tokens,
        cwd=workspace,
        can_use_tool=_build_can_use_tool(workspace, allowed_tools),
        hooks=_build_hooks(workspace, allowed_tools),
        setting_sources=["project"],
    )

    response_chunks: list[str] = []
    messages: list[dict[str, Any]] = []
    final: ResultMessage | None = None

    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for message in client.receive_response():
            messages.append(_message_to_dict(message))
            text = _extract_text(message)
            if text:
                response_chunks.append(text)
            if isinstance(message, ResultMessage):
                final = message

    response_text = "\n\n".join(chunk for chunk in response_chunks if chunk).strip()
    result_text = getattr(final, "result", None)

    return AgentRunResult(
        workspace=workspace,
        profile=profile,
        response_text=response_text,
        result_text=result_text,
        structured_output=getattr(final, "structured_output", None),
        session_id=getattr(final, "session_id", None),
        total_cost_usd=getattr(final, "total_cost_usd", None),
        num_turns=getattr(final, "num_turns", None),
        subtype=getattr(final, "subtype", None),
        is_error=bool(final is None or getattr(final, "is_error", True)),
        messages=messages,
        job_id=job_id,
    )
