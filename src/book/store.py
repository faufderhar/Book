from __future__ import annotations

import sqlite3
import threading
from datetime import date, datetime
from pathlib import Path

from book.models import (
    MAX_ENTRIES_PER_LIST,
    SNAPSHOT_MISSING,
    SNAPSHOT_OK,
    PlatformHalt,
    RankEntry,
    RankList,
    Snapshot,
)
from book.paths import sqlite_path


SCHEMA = """
CREATE TABLE IF NOT EXISTS rank_lists (
    platform TEXT NOT NULL,
    list_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    rank_kind TEXT NOT NULL,
    category TEXT NOT NULL,
    has_occupancy INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (platform, list_id)
);

CREATE TABLE IF NOT EXISTS snapshots (
    platform TEXT NOT NULL,
    list_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    status TEXT NOT NULL,
    halt_reason TEXT,
    PRIMARY KEY (platform, list_id, snapshot_date)
);

CREATE TABLE IF NOT EXISTS snapshot_entries (
    platform TEXT NOT NULL,
    list_id TEXT NOT NULL,
    snapshot_date TEXT NOT NULL,
    rank INTEGER NOT NULL,
    work_id TEXT NOT NULL,
    title TEXT NOT NULL,
    author TEXT NOT NULL,
    category TEXT NOT NULL,
    serial_status TEXT,
    metric_name TEXT,
    metric_value INTEGER,
    updated_at_on_page TEXT,
    PRIMARY KEY (platform, list_id, snapshot_date, rank)
);

CREATE TABLE IF NOT EXISTS platform_halts (
    platform TEXT PRIMARY KEY,
    reason TEXT NOT NULL,
    halted_at TEXT NOT NULL
);
"""


class Store:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or sqlite_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.connection = sqlite3.connect(self.path, check_same_thread=False, timeout=30)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(SCHEMA)

    def close(self) -> None:
        with self._lock:
            self.connection.close()

    def upsert_rank_list(self, rank_list: RankList) -> None:
        with self._lock:
            self.connection.execute(
                """
                INSERT INTO rank_lists (platform, list_id, channel, rank_kind, category, has_occupancy)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, list_id) DO UPDATE SET
                    channel=excluded.channel,
                    rank_kind=excluded.rank_kind,
                    category=excluded.category,
                    has_occupancy=excluded.has_occupancy
                """,
                (
                    rank_list.platform,
                    rank_list.list_id,
                    rank_list.channel,
                    rank_list.rank_kind,
                    rank_list.category,
                    1 if rank_list.has_occupancy else 0,
                ),
            )
            self.connection.commit()

    def list_rank_lists(self, platform: str | None = None) -> list[RankList]:
        with self._lock:
            if platform:
                rows = self.connection.execute(
                    "SELECT * FROM rank_lists WHERE platform=? ORDER BY channel, rank_kind, category",
                    (platform,),
                ).fetchall()
            else:
                rows = self.connection.execute(
                    "SELECT * FROM rank_lists ORDER BY platform, channel, rank_kind, category"
                ).fetchall()
        return [
            RankList(
                platform=row["platform"],
                list_id=row["list_id"],
                channel=row["channel"],
                rank_kind=row["rank_kind"],
                category=row["category"],
                has_occupancy=bool(row["has_occupancy"]),
            )
            for row in rows
        ]

    def replace_snapshot(self, snapshot: Snapshot) -> None:
        entries = tuple(snapshot.entries[:MAX_ENTRIES_PER_LIST])
        date_text = snapshot.snapshot_date.isoformat()
        with self._lock, self.connection:
            self.connection.execute(
                "DELETE FROM snapshot_entries WHERE platform=? AND list_id=? AND snapshot_date=?",
                (snapshot.platform, snapshot.list_id, date_text),
            )
            self.connection.execute(
                """
                INSERT INTO snapshots (platform, list_id, snapshot_date, captured_at, status, halt_reason)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, list_id, snapshot_date) DO UPDATE SET
                    captured_at=excluded.captured_at,
                    status=excluded.status,
                    halt_reason=excluded.halt_reason
                """,
                (
                    snapshot.platform,
                    snapshot.list_id,
                    date_text,
                    snapshot.captured_at.isoformat(),
                    snapshot.status,
                    snapshot.halt_reason,
                ),
            )
            self.connection.executemany(
                """
                INSERT INTO snapshot_entries (
                    platform, list_id, snapshot_date, rank, work_id, title, author,
                    category, serial_status, metric_name, metric_value, updated_at_on_page
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot.platform,
                        snapshot.list_id,
                        date_text,
                        entry.rank,
                        entry.work_id,
                        entry.title,
                        entry.author,
                        entry.category,
                        entry.serial_status,
                        entry.metric_name,
                        entry.metric_value,
                        entry.updated_at_on_page,
                    )
                    for entry in entries
                ],
            )

    def get_snapshot(self, platform: str, list_id: str, snapshot_date: date) -> Snapshot | None:
        with self._lock:
            header = self.connection.execute(
                "SELECT * FROM snapshots WHERE platform=? AND list_id=? AND snapshot_date=?",
                (platform, list_id, snapshot_date.isoformat()),
            ).fetchone()
            if header is None:
                return None
            rows = self.connection.execute(
                """
                SELECT * FROM snapshot_entries
                WHERE platform=? AND list_id=? AND snapshot_date=?
                ORDER BY rank
                """,
                (platform, list_id, snapshot_date.isoformat()),
            ).fetchall()
        entries = tuple(
            RankEntry(
                rank=row["rank"],
                work_id=row["work_id"],
                title=row["title"],
                author=row["author"],
                category=row["category"],
                serial_status=row["serial_status"],
                metric_name=row["metric_name"],
                metric_value=row["metric_value"],
                updated_at_on_page=row["updated_at_on_page"],
            )
            for row in rows
        )
        return Snapshot(
            platform=header["platform"],
            list_id=header["list_id"],
            snapshot_date=date.fromisoformat(header["snapshot_date"]),
            captured_at=datetime.fromisoformat(header["captured_at"]),
            entries=entries,
            status=header["status"],
            halt_reason=header["halt_reason"],
        )

    def previous_ok_snapshot(
        self, platform: str, list_id: str, snapshot_date: date
    ) -> Snapshot | None:
        with self._lock:
            row = self.connection.execute(
                """
                SELECT snapshot_date FROM snapshots
                WHERE platform=? AND list_id=? AND status=? AND snapshot_date < ?
                ORDER BY snapshot_date DESC
                LIMIT 1
                """,
                (platform, list_id, SNAPSHOT_OK, snapshot_date.isoformat()),
            ).fetchone()
            if row is None:
                return None
            previous_date = date.fromisoformat(row["snapshot_date"])
            # 进出只对比上一个自然日，不允许跨天硬凑。
            if (snapshot_date - previous_date).days != 1:
                return None
        return self.get_snapshot(platform, list_id, previous_date)

    def snapshot_dates(self) -> list[date]:
        with self._lock:
            rows = self.connection.execute(
                "SELECT DISTINCT snapshot_date FROM snapshots ORDER BY snapshot_date DESC"
            ).fetchall()
        return [date.fromisoformat(row["snapshot_date"]) for row in rows]

    def record_halt(self, halt: PlatformHalt) -> None:
        with self._lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO platform_halts (platform, reason, halted_at)
                VALUES (?, ?, ?)
                ON CONFLICT(platform) DO UPDATE SET
                    reason=excluded.reason,
                    halted_at=excluded.halted_at
                """,
                (halt.platform, halt.reason, halt.halted_at.isoformat()),
            )

    def clear_halt(self, platform: str) -> None:
        with self._lock, self.connection:
            self.connection.execute("DELETE FROM platform_halts WHERE platform=?", (platform,))

    def get_halt(self, platform: str) -> PlatformHalt | None:
        with self._lock:
            row = self.connection.execute(
                "SELECT * FROM platform_halts WHERE platform=?", (platform,)
            ).fetchone()
        if row is None:
            return None
        return PlatformHalt(
            platform=row["platform"],
            reason=row["reason"],
            halted_at=datetime.fromisoformat(row["halted_at"]),
        )

    def mark_missing(self, platform: str, list_id: str, snapshot_date: date, captured_at: datetime, reason: str) -> None:
        existing = self.get_snapshot(platform, list_id, snapshot_date)
        if existing is not None and existing.status == SNAPSHOT_OK:
            return
        self.replace_snapshot(
            Snapshot(
                platform=platform,
                list_id=list_id,
                snapshot_date=snapshot_date,
                captured_at=captured_at,
                entries=(),
                status=SNAPSHOT_MISSING,
                halt_reason=reason,
            )
        )
