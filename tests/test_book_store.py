from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from book.models import (
    PLATFORM_FANQIE,
    SNAPSHOT_MISSING,
    SNAPSHOT_OK,
    RankEntry,
    RankList,
    Snapshot,
)
from book.store import Store


def sample_entry(rank: int = 1, work_id: str = "w1") -> RankEntry:
    return RankEntry(
        rank=rank,
        work_id=work_id,
        title="书",
        author="甲",
        category="都市",
        metric_name="在读",
        metric_value=100,
    )


def ok_snapshot(day: date, entries: tuple[RankEntry, ...] | None = None) -> Snapshot:
    return Snapshot(
        platform=PLATFORM_FANQIE,
        list_id="1_2_8",
        snapshot_date=day,
        captured_at=datetime(2026, 8, 31, 15, 30),
        entries=entries if entries is not None else (sample_entry(),),
        status=SNAPSHOT_OK,
    )


class StoreSnapshotTest(unittest.TestCase):
    def test_replace_overwrites_same_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            day = date(2026, 8, 30)
            store.replace_snapshot(ok_snapshot(day, (sample_entry(1, "old"),)))
            store.replace_snapshot(ok_snapshot(day, (sample_entry(1, "new"),)))
            loaded = store.get_snapshot(PLATFORM_FANQIE, "1_2_8", day)
            self.assertEqual(loaded.entries[0].work_id, "new")
            self.assertEqual(loaded.status, SNAPSHOT_OK)

    def test_mark_missing_does_not_clobber_ok_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            day = date(2026, 8, 30)
            store.replace_snapshot(ok_snapshot(day, (sample_entry(1, "keep"),)))
            store.mark_missing(
                PLATFORM_FANQIE,
                "1_2_8",
                day,
                datetime(2026, 8, 31, 16, 0),
                "HTTP 403",
            )
            loaded = store.get_snapshot(PLATFORM_FANQIE, "1_2_8", day)
            self.assertEqual(loaded.status, SNAPSHOT_OK)
            self.assertEqual(loaded.entries[0].work_id, "keep")

    def test_mark_missing_writes_when_no_ok_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            day = date(2026, 8, 30)
            store.mark_missing(
                PLATFORM_FANQIE,
                "1_2_8",
                day,
                datetime(2026, 8, 31, 15, 30),
                "HTTP 403",
            )
            loaded = store.get_snapshot(PLATFORM_FANQIE, "1_2_8", day)
            self.assertEqual(loaded.status, SNAPSHOT_MISSING)
            self.assertEqual(loaded.entries, ())
            self.assertEqual(loaded.halt_reason, "HTTP 403")

    def test_previous_ok_requires_adjacent_calendar_day(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            store.replace_snapshot(ok_snapshot(date(2026, 8, 28)))
            self.assertIsNone(
                store.previous_ok_snapshot(PLATFORM_FANQIE, "1_2_8", date(2026, 8, 30))
            )
            store.replace_snapshot(ok_snapshot(date(2026, 8, 29)))
            previous = store.previous_ok_snapshot(PLATFORM_FANQIE, "1_2_8", date(2026, 8, 30))
            self.assertIsNotNone(previous)
            self.assertEqual(previous.snapshot_date, date(2026, 8, 29))

    def test_missing_yesterday_is_not_used_for_enter_leave(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            store.mark_missing(
                PLATFORM_FANQIE,
                "1_2_8",
                date(2026, 8, 29),
                datetime(2026, 8, 30, 15, 30),
                "失败",
            )
            store.replace_snapshot(ok_snapshot(date(2026, 8, 30)))
            self.assertIsNone(
                store.previous_ok_snapshot(PLATFORM_FANQIE, "1_2_8", date(2026, 8, 30))
            )


class RankListStoreTest(unittest.TestCase):
    def test_upsert_rank_list(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            store.upsert_rank_list(
                RankList(
                    platform=PLATFORM_FANQIE,
                    list_id="1_2_8",
                    channel="male",
                    rank_kind="read",
                    category="都市日常",
                )
            )
            lists = store.list_rank_lists(PLATFORM_FANQIE)
            self.assertEqual(len(lists), 1)
            self.assertEqual(lists[0].category, "都市日常")
            self.assertFalse(lists[0].has_occupancy)


if __name__ == "__main__":
    unittest.main()
