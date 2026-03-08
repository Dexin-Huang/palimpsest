# Agent Workers

Purpose: give future sessions a simple, reliable way to offload narrow local
repo tasks to Claude SDK "grunt workers" without losing sight of Palimpsest's
actual job.

North star reminder:
- Palimpsest exists to recover neglected knowledge traditions from archival
  corpora.
- These workers are support tools.
- Use them to reduce local mechanical work, not to replace the main session's
  judgment about manuscripts, research direction, or product strategy.

## When To Use Them

Good uses:
- inspect a code path
- find a symbol or config value
- apply a narrow patch
- summarize a local note or output file
- run several independent local tasks in parallel
- do a bounded web lookup when `--with-web-search` is explicitly enabled

Bad uses:
- broad product planning
- open-ended research synthesis
- manuscript interpretation that needs sustained judgment
- anything that requires shell access

Rule of thumb:
- main session handles strategy, review, and research interpretation
- workers handle bounded local grunt work

## Commands

Single worker commands:

```bash
python -m palimpsest agent "General local helper task"
python -m palimpsest agent-edit "Apply the requested patch"
python -m palimpsest agent-inspect "Find the file containing DEFAULT_MODEL_AGENT"
python -m palimpsest agent-summarize "Summarize the local notes in two sentences"
python -m palimpsest agent-inspect --with-web-search "Find the current manuscript viewer URL"
```

Concurrent batch runner:

```bash
python -m palimpsest agent-batch --input jobs.json --concurrency 3 --json
```

All worker commands support:
- `--workspace`
- `--model`
- `--max-turns`
- `--max-budget-usd`
- `--max-thinking-tokens`
- `--permission-mode`
- `--with-web-search`
- `--json`

## Profiles

`agent`
- General helper.
- Can read and edit local files.
- Use when the task is small but does not fit a stricter lane.

`agent-edit`
- Editing worker.
- Use for isolated file changes.
- Best when you already know roughly what needs to change.

`agent-inspect`
- Read-only inspection worker.
- Use for finding symbols, tracing code paths, or extracting exact lines.

`agent-summarize`
- Read-only summarizer.
- Use for compressing local notes, outputs, or docs into a short answer.

`agent-batch`
- Runs multiple independent jobs concurrently.
- Best when tasks do not depend on each other.

## Safety Model

The worker wrapper is intentionally constrained:
- workspace-scoped file access
- no default `Bash`
- no default `WebSearch` unless `--with-web-search` is passed
- read-only profiles cannot edit files
- out-of-workspace file reads are denied

This means the workers are suitable for local repo help, not general computer
control.

## Prompting Guidance

Write prompts like work tickets, not conversations.

Good:
- `Find the file containing X and reply with the filename and exact line.`
- `Update config.py so DEFAULT_FOO uses BAR. Reply with one sentence.`
- `Summarize notes.md in two sentences.`
- `With web search enabled, find the current official viewer URL and reply with one link.`

Bad:
- `Look around and see what seems important.`
- `Figure out the architecture and propose improvements.`
- `Research the best approach on the internet.`

Prefer:
- one concrete task
- one workspace
- one expected output shape

## Batch Format

Input may be `json` or `jsonl`.

Example:

```json
[
  {
    "id": "inspect-1",
    "profile": "inspect",
    "workspace": "D:/Projects/palimpsest",
    "prompt": "Find the file containing DEFAULT_MODEL_AGENT."
  },
  {
    "id": "edit-1",
    "profile": "edit",
    "workspace": "D:/Projects/palimpsest",
    "prompt": "Update the requested file and reply with one sentence."
  },
  {
    "id": "summ-1",
    "profile": "summarize",
    "workspace": "D:/Projects/palimpsest/docs",
    "prompt": "Summarize the main idea of knowledge_recovery_vision.md in two sentences."
  }
]
```

Recognized per-job fields:
- `id`
- `profile`
- `workspace`
- `prompt`
- `with_web_search`
- `model`
- `max_turns`
- `max_budget_usd`
- `max_thinking_tokens`
- `permission_mode`

Batch behavior:
- jobs run concurrently up to `--concurrency`
- results are returned in input order
- exit code is non-zero if any job errors

## New Session Playbook

If you are a new session and need to use these tools:

1. Read this file.
2. Identify which parts of the task are mechanical and independent.
3. Keep the main session focused on Palimpsest's core task.
4. Use `agent-inspect` to gather exact local facts.
5. Use `agent-edit` for isolated changes.
6. Use `agent-summarize` to compress outputs or notes.
7. Use `agent-batch` only for truly independent jobs.
8. Return to the main thread with the worker outputs and continue the real task.

## Core Task Reminder

These workers exist to help the main session do more of the following:
- discover under-studied manuscripts
- process page images into reliable evidence
- interpret interesting transcriptions
- extract claims, recipes, stories, places, and technical knowledge
- build Palimpsest into a knowledge-recovery system

If you are choosing between "spawn a worker" and "think about the manuscripts,"
default to thinking about the manuscripts unless the subtask is obviously
mechanical.
