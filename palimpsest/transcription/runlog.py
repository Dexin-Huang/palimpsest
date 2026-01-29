import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Optional

from .io import atomic_write_text


def get_git_commit(project_root: Path) -> Optional[str]:
    try:
        import subprocess

        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:
        return None


class RunLogger:
    def __init__(self, runs_dir: Path, run_id: str) -> None:
        self.runs_dir = runs_dir
        self.run_id = run_id
        self.events_path = runs_dir / "events.jsonl"
        self.errors_path = runs_dir / "errors.jsonl"
        self.status_path = runs_dir / "status.json"
        self.lock = threading.Lock()

    def _append(self, path: Path, payload: dict) -> None:
        line = json.dumps(payload, ensure_ascii=True)
        with self.lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
                os.fsync(handle.fileno())

    def event(self, payload: dict) -> None:
        self._append(self.events_path, payload)

    def error(self, payload: dict) -> None:
        self._append(self.errors_path, payload)

    def write_status(self, payload: dict) -> None:
        with self.lock:
            atomic_write_text(self.status_path, json.dumps(payload, indent=2))

    def report(self, name: str, payload: dict) -> None:
        path = self.runs_dir / f"{name}.jsonl"
        self._append(path, payload)


def start_run(out_dir: Path, run_meta: dict, pass1_prompt: str, pass2_prompt: str) -> tuple[str, RunLogger, Path]:
    runs_dir = out_dir / "_runs"
    trace_dir = out_dir / "_traces"
    runs_dir.mkdir(parents=True, exist_ok=True)
    trace_dir.mkdir(parents=True, exist_ok=True)
    run_id = run_meta["run_id"]
    atomic_write_text(runs_dir / f"run_{run_id}.json", json.dumps(run_meta, indent=2))
    atomic_write_text(runs_dir / f"run_{run_id}_pass1.txt", pass1_prompt)
    atomic_write_text(runs_dir / f"run_{run_id}_pass2.txt", pass2_prompt)
    logger = RunLogger(runs_dir, run_id)
    logger.event({
        "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "run_id": run_id,
        "stage": "run",
        "status": "start",
        "images": run_meta.get("image_count"),
    })
    return run_id, logger, trace_dir


def build_status_snapshot(out_dir: Path, expected_stems: list[str]) -> dict:
    pass1_stems = {p.stem[:-6] for p in out_dir.glob("*_pass1.json")}
    final_stems = {p.stem[:-6] for p in out_dir.glob("*_final.json")}
    expected = set(expected_stems)
    return {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "expected_total": len(expected),
        "pass1_total": len(pass1_stems),
        "final_total": len(final_stems),
        "missing_pass1": sorted(expected - pass1_stems),
        "pass1_only": sorted(pass1_stems - final_stems),
        "missing_pass2": sorted(expected - final_stems),
        "complete": sorted(final_stems),
    }
