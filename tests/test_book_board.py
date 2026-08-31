from __future__ import annotations

import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from book.board import build_day_board
from book.models import (
    PLATFORM_FANQIE,
    SNAPSHOT_OK,
    PlatformHalt,
    RankEntry,
    RankList,
    Snapshot,
)
from book.store import Store
from book.web.app import create_app


def seed_list(store: Store) -> None:
    store.upsert_rank_list(
        RankList(
            platform=PLATFORM_FANQIE,
            list_id="1_2_8",
            channel="male",
            rank_kind="read",
            category="都市日常",
        )
    )


def ok_day(store: Store, day: date, work_id: str = "w1") -> None:
    store.replace_snapshot(
        Snapshot(
            platform=PLATFORM_FANQIE,
            list_id="1_2_8",
            snapshot_date=day,
            captured_at=datetime(2026, 8, 31, 15, 30),
            entries=(
                RankEntry(
                    rank=1,
                    work_id=work_id,
                    title="书",
                    author="甲",
                    category="都市日常",
                    metric_name="在读",
                    metric_value=1000,
                ),
            ),
            status=SNAPSHOT_OK,
        )
    )


class DayBoardTest(unittest.TestCase):
    def test_missing_yesterday_shows_none_not_zero(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            seed_list(store)
            ok_day(store, date(2026, 8, 30))
            board = build_day_board(store, date(2026, 8, 30))
            row = board.groups[0][1][0]
            self.assertIsNone(row.entered_count)
            self.assertIsNone(row.left_count)
            self.assertFalse(row.missing)

    def test_adjacent_days_count_enter_leave(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            seed_list(store)
            ok_day(store, date(2026, 8, 29), "old")
            ok_day(store, date(2026, 8, 30), "new")
            board = build_day_board(store, date(2026, 8, 30))
            row = board.groups[0][1][0]
            self.assertEqual(row.entered_count, 1)
            self.assertEqual(row.left_count, 1)

    def test_halt_banner_only_on_days_with_missing_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            seed_list(store)
            ok_day(store, date(2026, 8, 29))
            store.mark_missing(
                PLATFORM_FANQIE,
                "1_2_8",
                date(2026, 8, 30),
                datetime(2026, 8, 31, 15, 30),
                "HTTP 403",
            )
            store.record_halt(
                PlatformHalt(
                    platform=PLATFORM_FANQIE,
                    reason="HTTP 403",
                    halted_at=datetime(2026, 8, 31, 15, 30),
                )
            )
            failed = build_day_board(store, date(2026, 8, 30))
            self.assertEqual(failed.halt_reason, "HTTP 403")
            historical = build_day_board(store, date(2026, 8, 29))
            self.assertIsNone(historical.halt_reason)


class BoardWebTest(unittest.TestCase):
    def test_invalid_day_is_400(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            client = TestClient(create_app(store))
            response = client.get("/?day=not-a-date")
            self.assertEqual(response.status_code, 400)

    def test_summary_renders_missing_copy(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            seed_list(store)
            store.mark_missing(
                PLATFORM_FANQIE,
                "1_2_8",
                date(2026, 8, 30),
                datetime(2026, 8, 31, 15, 30),
                "接口空列表",
            )
            client = TestClient(create_app(store))
            response = client.get("/?day=2026-08-30")
            self.assertEqual(response.status_code, 200)
            self.assertIn("当天无有效快照，进出不计算", response.text)
            self.assertIn("接口空列表", response.text)


if __name__ == "__main__":
    unittest.main()
