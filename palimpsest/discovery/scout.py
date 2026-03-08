from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from palimpsest.agent_sdk import run_agent_prompt
from palimpsest.discovery import DiscoveryDB


def utc_now_slug() -> str:
    return datetime.utcnow().strftime("%Y%m%d_%H%M%S")


@dataclass
class ScoutCandidate:
    manuscript_id: str
    shelfmark: str
    repository: str
    collection: str | None
    title: str | None
    date_range: str | None
    languages: list[str] | None
    subject_areas: list[str] | None
    description: str | None
    iiif_manifest_url: str | None
    source_catalog: dict[str, Any] | None
    initial_score: int | None
    interest_score: int | None
    interest_reason: str | None
    triage_method: str | None
    triage_model: str | None
    triage_json: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "manuscript_id": self.manuscript_id,
            "shelfmark": self.shelfmark,
            "repository": self.repository,
            "collection": self.collection,
            "title": self.title,
            "date_range": self.date_range,
            "languages": self.languages,
            "subject_areas": self.subject_areas,
            "description": self.description,
            "iiif_manifest_url": self.iiif_manifest_url,
            "source_catalog": self.source_catalog,
            "triage": {
                "initial_score": self.initial_score,
                "interest_score": self.interest_score,
                "interest_reason": self.interest_reason,
                "triage_method": self.triage_method,
                "triage_model": self.triage_model,
                "triage_json": self.triage_json,
            },
        }


def collect_candidates(
    db: DiscoveryDB,
    *,
    repository: str | None = None,
    collection: str | None = None,
    min_score: int = 7,
    limit: int = 12,
) -> list[ScoutCandidate]:
    candidates: list[ScoutCandidate] = []
    manuscripts = db.list_manuscripts(limit=10000)

    for ms in manuscripts:
        if repository and ms.repository != repository:
            continue
        if collection and ms.collection != collection:
            continue

        opp = db.get_opportunity(ms.id)
        if not opp or opp.interest_score is None:
            continue
        if opp.interest_score < min_score:
            continue

        candidates.append(
            ScoutCandidate(
                manuscript_id=ms.id,
                shelfmark=ms.shelfmark,
                repository=ms.repository,
                collection=ms.collection,
                title=ms.title,
                date_range=ms.date_range,
                languages=ms.languages,
                subject_areas=ms.subject_areas,
                description=ms.description,
                iiif_manifest_url=ms.iiif_manifest_url,
                source_catalog=ms.source_catalog,
                initial_score=opp.initial_score,
                interest_score=opp.interest_score,
                interest_reason=opp.interest_reason,
                triage_method=opp.triage_method,
                triage_model=opp.triage_model,
                triage_json=opp.triage_json,
            )
        )

    candidates.sort(
        key=lambda item: (
            item.interest_score or 0,
            item.initial_score or 0,
            item.date_range or "",
            item.shelfmark,
        ),
        reverse=True,
    )
    return candidates[:limit]


def build_scout_prompt(
    *,
    repository: str | None,
    collection: str | None,
    min_score: int,
    candidates_path: Path,
) -> str:
    repo_scope = repository or "all repositories"
    collection_scope = collection or "all collections"
    return (
        "Read candidates.json and produce a scouting memo.\n\n"
        "Mission: identify which candidates best match Palimpsest's north star: "
        "recovering traces of vanished human worlds.\n\n"
        "Prioritize:\n"
        "- stories\n"
        "- ritual and mysticism\n"
        "- medicine and embodied practice\n"
        "- divination, omens, cosmology, calendrics\n"
        "- travel, local memory, lived texture\n\n"
        "Deprioritize:\n"
        "- dictionaries, glossaries, phrasebooks\n"
        "- generic respectable commentary without much human texture\n"
        "- catalog entries whose payoff is mostly prestige\n\n"
        "Requirements:\n"
        "- Ground claims in the provided metadata first.\n"
        "- Distinguish direct evidence from probable inference.\n"
        "- If you use web search, use it only to sharpen ranking, not to invent content.\n"
        "- Prefer candidates that seem underdescribed but alive.\n\n"
        f"Scope:\n- repository: {repo_scope}\n- collection: {collection_scope}\n- min score: {min_score}\n\n"
        "Output markdown with exactly these sections:\n"
        "1. Top Picks\n"
        "2. Why They Matter\n"
        "3. Caution Flags\n"
        "4. Next Intake Order\n\n"
        "For Top Picks, rank up to 5 candidates with manuscript_id, shelfmark, one-line thesis, "
        "and a short evidence note.\n\n"
        f"Candidate bundle: {candidates_path.name}\n"
    )


def write_scout_workspace(
    out_dir: Path,
    candidates: list[ScoutCandidate],
    prompt: str,
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    candidates_path = out_dir / "candidates.json"
    prompt_path = out_dir / "prompt.txt"

    payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "candidates": [candidate.as_dict() for candidate in candidates],
    }
    candidates_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    prompt_path.write_text(prompt, encoding="utf-8")
    return candidates_path, prompt_path


async def run_candidate_scout(
    *,
    db_path: Path,
    out_dir: Path,
    repository: str | None,
    collection: str | None,
    min_score: int,
    limit: int,
    model: str,
    with_web_search: bool,
    max_turns: int,
    max_budget_usd: float | None,
    max_thinking_tokens: int | None,
    permission_mode: str,
) -> dict[str, Any]:
    db = DiscoveryDB(db_path)
    try:
        candidates = collect_candidates(
            db,
            repository=repository,
            collection=collection,
            min_score=min_score,
            limit=limit,
        )
    finally:
        db.close()

    if not candidates:
        raise ValueError("No candidates matched the scout filters")

    prompt = build_scout_prompt(
        repository=repository,
        collection=collection,
        min_score=min_score,
        candidates_path=Path("candidates.json"),
    )
    candidates_path, prompt_path = write_scout_workspace(out_dir, candidates, prompt)

    result = await run_agent_prompt(
        prompt=prompt,
        workspace=out_dir,
        model=model,
        profile="candidate_scout",
        with_web_search=with_web_search,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        max_thinking_tokens=max_thinking_tokens,
        permission_mode=permission_mode,
    )

    memo_text = result.response_text or (result.result_text or "").strip()
    memo_path = out_dir / "scout_memo.md"
    memo_path.write_text(memo_text, encoding="utf-8")

    meta_path = out_dir / "scout_meta.json"
    meta_payload = {
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "db_path": str(db_path),
        "repository": repository,
        "collection": collection,
        "min_score": min_score,
        "limit": limit,
        "candidate_count": len(candidates),
        "model": model,
        "with_web_search": with_web_search,
        "prompt_path": str(prompt_path),
        "candidates_path": str(candidates_path),
        "memo_path": str(memo_path),
        "session_id": result.session_id,
        "total_cost_usd": result.total_cost_usd,
        "num_turns": result.num_turns,
        "is_error": result.is_error,
    }
    meta_path.write_text(json.dumps(meta_payload, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "memo_path": memo_path,
        "meta_path": meta_path,
        "candidates_path": candidates_path,
        "candidate_count": len(candidates),
        "result": result,
    }
