"""Real downstream acceptance check for an Alexandria publication consumer."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class ConsumerCanaryError(RuntimeError):
    pass


def verify_alexandria(
    bundle_source: str | Path,
    consumer_root: Path,
    *,
    timeout_seconds: int = 1800,
) -> None:
    """Import one bundle through Alexandria and complete its production build."""
    root = consumer_root.resolve()
    if not (root / "package.json").is_file():
        raise ConsumerCanaryError(f"Alexandria package.json not found under {root}")
    bun = shutil.which("bun")
    if bun is None:
        raise ConsumerCanaryError("bun is required for the Alexandria consumer canary")
    source_value = str(bundle_source)
    source = (
        source_value
        if source_value.lower().startswith(("http://", "https://"))
        else str(Path(source_value).resolve())
    )
    _run((bun, "run", "import-library", source), root, timeout_seconds)
    _run((bun, "run", "build"), root, timeout_seconds)


def _run(command: tuple[str, ...], root: Path, timeout_seconds: int) -> None:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_seconds,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ConsumerCanaryError(
            f"Consumer command failed to start or finish: {error}"
        ) from error
    if result.returncode:
        output = result.stdout[-4000:]
        raise ConsumerCanaryError(
            f"Consumer command exited {result.returncode}: {' '.join(command)}\n{output}"
        )
