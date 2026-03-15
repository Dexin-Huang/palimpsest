from __future__ import annotations

import sqlite3
from typing import Any


def collect_discovery_stats(conn: sqlite3.Connection) -> dict[str, Any]:
    """Collect high-level discovery stats across canonical and compatibility tables."""
    cursor = conn.cursor()

    cursor.execute(
        """
        SELECT status, COUNT(*) as count
        FROM manuscripts
        GROUP BY status
        """
    )
    status_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) as count FROM manuscripts")
    total = cursor.fetchone()["count"]

    cursor.execute(
        """
        SELECT obscurity_score, COUNT(*) as count
        FROM manuscripts
        WHERE obscurity_score IS NOT NULL
        GROUP BY obscurity_score
        ORDER BY obscurity_score
        """
    )
    obscurity_dist = {row["obscurity_score"]: row["count"] for row in cursor.fetchall()}

    cursor.execute(
        """
        SELECT status, COUNT(*) as count
        FROM our_work
        GROUP BY status
        """
    )
    work_counts = {row["status"]: row["count"] for row in cursor.fetchall()}

    cursor.execute(
        """
        SELECT action, COUNT(*) as count
        FROM audit_log
        WHERE timestamp > datetime('now', '-7 days')
        GROUP BY action
        """
    )
    recent_actions = {row["action"]: row["count"] for row in cursor.fetchall()}

    return {
        "total_manuscripts": total,
        "by_status": status_counts,
        "by_obscurity": obscurity_dist,
        "work_progress": work_counts,
        "recent_actions": recent_actions,
    }


__all__ = ["collect_discovery_stats"]
