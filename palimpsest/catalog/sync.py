"""Drive one source head into the catalog revision store."""

from __future__ import annotations

from dataclasses import dataclass

from palimpsest.catalog.database import CatalogDB
from palimpsest.catalog.heads import build_head


@dataclass(frozen=True)
class SyncResult:
    sync_id: int
    source_id: str
    records_seen: int
    records_inserted: int
    records_revised: int
    records_unchanged: int
    records_revived: int
    records_tombstoned: int


def sync_source(
    database: CatalogDB,
    source_id: str,
    *,
    resume: bool = False,
) -> SyncResult:
    source = database.source(source_id)
    head = build_head(source.head, source.config)
    run = database.begin_sync(source_id, resume=resume)
    try:
        if not run.reached_end:
            for page in head.pages(run.cursor):
                database.apply_page(run, page)
        database.complete_sync(run)
    except BaseException as error:
        database.fail_sync(run, error)
        raise
    row = database.sync_run(run.sync_id)
    return SyncResult(
        sync_id=run.sync_id,
        source_id=source_id,
        records_seen=row["records_seen"],
        records_inserted=row["records_inserted"],
        records_revised=row["records_revised"],
        records_unchanged=row["records_unchanged"],
        records_revived=row["records_revived"],
        records_tombstoned=row["records_tombstoned"],
    )
