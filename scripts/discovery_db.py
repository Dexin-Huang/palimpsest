#!/usr/bin/env python3
"""CLI for Discovery Database operations.

Usage:
    python scripts/discovery_db.py import --jsonl discovery/registry/manuscripts.jsonl
    python scripts/discovery_db.py next --min-obscurity 7
    python scripts/discovery_db.py log --id vat_pal_lat_1985 --action analyzed
    python scripts/discovery_db.py stats
    python scripts/discovery_db.py list --status discovered
    python scripts/discovery_db.py show --id vat_pal_lat_1267
    python scripts/discovery_db.py export --output discovery/export.jsonl
"""

import argparse
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from palimpsest.discovery import DiscoveryDB, Manuscript, AuditEntry


DEFAULT_DB = Path(__file__).parent.parent / "discovery" / "manuscripts.db"


def cmd_import(args):
    """Import manuscripts from JSONL file."""
    db = DiscoveryDB(args.db)

    for jsonl_path in args.jsonl:
        path = Path(jsonl_path)
        if not path.exists():
            print(f"File not found: {path}", file=sys.stderr)
            continue

        count = db.import_jsonl(path, agent="cli_import")
        print(f"Imported {count} manuscripts from {path}")

    db.close()


def cmd_next(args):
    """Get next priority manuscript to work on."""
    db = DiscoveryDB(args.db)

    ms = db.get_next_priority(
        min_obscurity=args.min_obscurity,
        status=args.status,
    )

    if ms:
        print(f"\n{'='*60}")
        print(f"  NEXT MANUSCRIPT: {ms.shelfmark}")
        print(f"{'='*60}")
        print(f"  ID:           {ms.id}")
        print(f"  Repository:   {ms.repository}")
        print(f"  Status:       {ms.status}")
        print(f"  Obscurity:    {ms.obscurity_score}/10")
        print(f"  WTF Factor:   {ms.wtf_score}/10")
        print(f"  Priority:     {ms.priority}")
        if ms.title:
            print(f"  Title:        {ms.title}")
        if ms.date_range:
            print(f"  Date:         {ms.date_range}")
        if ms.description:
            print(f"  Description:  {ms.description[:100]}...")
        if ms.iiif_manifest_url:
            print(f"  IIIF:         {ms.iiif_manifest_url}")
        print(f"{'='*60}\n")

        if args.json:
            print(json.dumps({
                "id": ms.id,
                "shelfmark": ms.shelfmark,
                "repository": ms.repository,
                "iiif_manifest_url": ms.iiif_manifest_url,
                "obscurity_score": ms.obscurity_score,
                "wtf_score": ms.wtf_score,
            }, indent=2))
    else:
        print("No manuscripts match the criteria.")

    db.close()


def cmd_log(args):
    """Log an action on a manuscript."""
    db = DiscoveryDB(args.db)

    # Parse details if provided
    details = None
    if args.details:
        try:
            details = json.loads(args.details)
        except json.JSONDecodeError:
            details = {"note": args.details}

    db.log_action(
        manuscript_id=args.id,
        action=args.action,
        agent=args.agent,
        details=details,
    )

    print(f"Logged: {args.action} on {args.id} by {args.agent}")

    # Optionally update status
    if args.set_status:
        db.update_manuscript(args.id, {"status": args.set_status}, agent=args.agent)
        print(f"Updated status to: {args.set_status}")

    db.close()


def cmd_stats(args):
    """Show database statistics."""
    db = DiscoveryDB(args.db)
    stats = db.get_stats()

    print("\n" + "="*50)
    print("  PALIMPSEST DISCOVERY DATABASE")
    print("="*50)

    print(f"\n  Total Manuscripts: {stats['total_manuscripts']}")

    print("\n  By Status:")
    for status, count in sorted(stats.get("by_status", {}).items()):
        bar = "#" * min(count * 2, 30)
        print(f"    {status:15} {count:4}  {bar}")

    print("\n  By Obscurity Score:")
    for score, count in sorted(stats.get("by_obscurity", {}).items()):
        bar = "#" * min(count * 2, 30)
        print(f"    {score:2}/10  {count:4}  {bar}")

    if stats.get("work_progress"):
        print("\n  Work Progress:")
        for status, count in sorted(stats.get("work_progress", {}).items()):
            bar = "#" * min(count * 2, 30)
            print(f"    {status:15} {count:4}  {bar}")

    if stats.get("recent_actions"):
        print("\n  Recent Activity (7 days):")
        for action, count in sorted(stats.get("recent_actions", {}).items()):
            print(f"    {action:20} {count:4}")

    print("\n" + "="*50 + "\n")

    db.close()


def cmd_list(args):
    """List manuscripts."""
    db = DiscoveryDB(args.db)

    manuscripts = db.list_manuscripts(
        status=args.status,
        min_obscurity=args.min_obscurity,
        limit=args.limit,
    )

    if args.json:
        print(json.dumps([{
            "id": ms.id,
            "shelfmark": ms.shelfmark,
            "status": ms.status,
            "obscurity_score": ms.obscurity_score,
            "wtf_score": ms.wtf_score,
        } for ms in manuscripts], indent=2))
    else:
        print(f"\n{'ID':<25} {'Shelfmark':<20} {'Status':<15} {'Obs':>4} {'WTF':>4}")
        print("-" * 75)
        for ms in manuscripts:
            print(f"{ms.id:<25} {ms.shelfmark:<20} {ms.status:<15} {ms.obscurity_score or '-':>4} {ms.wtf_score or '-':>4}")
        print(f"\nTotal: {len(manuscripts)}")

    db.close()


def cmd_show(args):
    """Show details for a specific manuscript."""
    db = DiscoveryDB(args.db)

    ms = db.get_manuscript(args.id)
    if not ms:
        ms = db.get_manuscript_by_shelfmark(args.id)

    if not ms:
        print(f"Manuscript not found: {args.id}", file=sys.stderr)
        db.close()
        return

    if args.json:
        print(json.dumps({
            "id": ms.id,
            "shelfmark": ms.shelfmark,
            "repository": ms.repository,
            "collection": ms.collection,
            "title": ms.title,
            "date_range": ms.date_range,
            "status": ms.status,
            "priority": ms.priority,
            "obscurity_score": ms.obscurity_score,
            "wtf_score": ms.wtf_score,
            "languages": ms.languages,
            "subject_areas": ms.subject_areas,
            "description": ms.description,
            "iiif_manifest_url": ms.iiif_manifest_url,
            "canvas_count": ms.canvas_count,
            "discovered_at": ms.discovered_at,
            "updated_at": ms.updated_at,
        }, indent=2))
    else:
        print(f"\n{'='*60}")
        print(f"  {ms.shelfmark}")
        print(f"{'='*60}")
        print(f"  ID:           {ms.id}")
        print(f"  Repository:   {ms.repository}")
        if ms.collection:
            print(f"  Collection:   {ms.collection}")
        if ms.title:
            print(f"  Title:        {ms.title}")
        if ms.date_range:
            print(f"  Date:         {ms.date_range}")
        print(f"  Status:       {ms.status}")
        print(f"  Priority:     {ms.priority}")
        print(f"  Obscurity:    {ms.obscurity_score}/10")
        print(f"  WTF Factor:   {ms.wtf_score}/10")
        if ms.languages:
            print(f"  Languages:    {', '.join(ms.languages)}")
        if ms.subject_areas:
            print(f"  Subjects:     {', '.join(ms.subject_areas)}")
        if ms.description:
            print(f"  Description:  {ms.description}")
        if ms.iiif_manifest_url:
            print(f"  IIIF:         {ms.iiif_manifest_url}")
            print(f"  Canvases:     {ms.canvas_count}")

        # Scholarship
        scholarship = db.get_scholarship(ms.id)
        if scholarship:
            print(f"\n  Scholarship:")
            for s in scholarship:
                print(f"    - {s.source}")
                if s.citation:
                    print(f"      {s.citation}")

        # Work progress
        work = db.get_work(ms.id)
        if work:
            stats = db.get_work_stats(ms.id)
            total = sum(stats.values())
            complete = stats.get("complete", 0)
            print(f"\n  Work Progress: {complete}/{total} pages complete")

        # Recent audit log
        audit = db.get_audit_log(ms.id, limit=5)
        if audit:
            print(f"\n  Recent Activity:")
            for entry in audit:
                print(f"    {entry.timestamp[:10]} {entry.action} ({entry.agent})")

        print(f"{'='*60}\n")

    db.close()


def cmd_export(args):
    """Export database to JSONL."""
    db = DiscoveryDB(args.db)
    count = db.export_jsonl(args.output)
    print(f"Exported {count} manuscripts to {args.output}")
    db.close()


def cmd_update(args):
    """Update a manuscript's fields."""
    db = DiscoveryDB(args.db)

    updates = {}
    if args.status:
        updates["status"] = args.status
    if args.priority is not None:
        updates["priority"] = args.priority
    if args.obscurity is not None:
        updates["obscurity_score"] = args.obscurity
    if args.wtf is not None:
        updates["wtf_score"] = args.wtf

    if not updates:
        print("No updates specified.", file=sys.stderr)
        db.close()
        return

    success = db.update_manuscript(args.id, updates, agent=args.agent)
    if success:
        print(f"Updated {args.id}: {updates}")
    else:
        print(f"Manuscript not found: {args.id}", file=sys.stderr)

    db.close()


def cmd_audit(args):
    """Show audit log."""
    db = DiscoveryDB(args.db)

    entries = db.get_audit_log(manuscript_id=args.id, limit=args.limit)

    if args.json:
        print(json.dumps([{
            "id": e.id,
            "manuscript_id": e.manuscript_id,
            "timestamp": e.timestamp,
            "action": e.action,
            "agent": e.agent,
            "details": json.loads(e.details) if e.details else None,
        } for e in entries], indent=2))
    else:
        print(f"\n{'Timestamp':<22} {'Manuscript':<20} {'Action':<15} {'Agent':<15}")
        print("-" * 75)
        for e in entries:
            print(f"{e.timestamp[:19]:<22} {e.manuscript_id:<20} {e.action:<15} {e.agent:<15}")
        print(f"\nTotal: {len(entries)}")

    db.close()


def main():
    parser = argparse.ArgumentParser(
        description="Palimpsest Discovery Database CLI"
    )
    parser.add_argument(
        "--db", type=str, default=str(DEFAULT_DB),
        help=f"Path to SQLite database (default: {DEFAULT_DB})"
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to run")

    # import command
    p_import = subparsers.add_parser("import", help="Import from JSONL")
    p_import.add_argument("--jsonl", nargs="+", required=True, help="JSONL files to import")
    p_import.set_defaults(func=cmd_import)

    # next command
    p_next = subparsers.add_parser("next", help="Get next priority manuscript")
    p_next.add_argument("--min-obscurity", type=int, default=7, help="Min obscurity score")
    p_next.add_argument("--status", default="discovered", help="Status filter")
    p_next.add_argument("--json", action="store_true", help="Output as JSON")
    p_next.set_defaults(func=cmd_next)

    # log command
    p_log = subparsers.add_parser("log", help="Log an action")
    p_log.add_argument("--id", required=True, help="Manuscript ID")
    p_log.add_argument("--action", required=True, help="Action (discovered/analyzed/etc)")
    p_log.add_argument("--agent", default="human", help="Agent performing action")
    p_log.add_argument("--details", help="JSON details or note")
    p_log.add_argument("--set-status", help="Also update manuscript status")
    p_log.set_defaults(func=cmd_log)

    # stats command
    p_stats = subparsers.add_parser("stats", help="Show database statistics")
    p_stats.set_defaults(func=cmd_stats)

    # list command
    p_list = subparsers.add_parser("list", help="List manuscripts")
    p_list.add_argument("--status", help="Filter by status")
    p_list.add_argument("--min-obscurity", type=int, help="Min obscurity score")
    p_list.add_argument("--limit", type=int, default=50, help="Max results")
    p_list.add_argument("--json", action="store_true", help="Output as JSON")
    p_list.set_defaults(func=cmd_list)

    # show command
    p_show = subparsers.add_parser("show", help="Show manuscript details")
    p_show.add_argument("--id", required=True, help="Manuscript ID or shelfmark")
    p_show.add_argument("--json", action="store_true", help="Output as JSON")
    p_show.set_defaults(func=cmd_show)

    # export command
    p_export = subparsers.add_parser("export", help="Export to JSONL")
    p_export.add_argument("--output", required=True, help="Output file path")
    p_export.set_defaults(func=cmd_export)

    # update command
    p_update = subparsers.add_parser("update", help="Update manuscript fields")
    p_update.add_argument("--id", required=True, help="Manuscript ID")
    p_update.add_argument("--status", help="New status")
    p_update.add_argument("--priority", type=int, help="New priority (1-100)")
    p_update.add_argument("--obscurity", type=int, help="New obscurity score (1-10)")
    p_update.add_argument("--wtf", type=int, help="New WTF score (1-10)")
    p_update.add_argument("--agent", default="cli", help="Agent making update")
    p_update.set_defaults(func=cmd_update)

    # audit command
    p_audit = subparsers.add_parser("audit", help="Show audit log")
    p_audit.add_argument("--id", help="Filter by manuscript ID")
    p_audit.add_argument("--limit", type=int, default=50, help="Max entries")
    p_audit.add_argument("--json", action="store_true", help="Output as JSON")
    p_audit.set_defaults(func=cmd_audit)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
