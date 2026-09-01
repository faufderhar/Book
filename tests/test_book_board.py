from __future__ import annotations

import threading
import tempfile
import time
import unittest
from datetime import date, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from book.board import build_day_board, format_sync_status
from book.models import (
    PLATFORM_FANQIE,
    SNAPSHOT_OK,
    PlatformHalt,
    RankEntry,
    RankList,
    Snapshot,
)
from book.store import Store
from book.sync import JOB_DONE, JOB_FAILED, get_job, reset_jobs
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
    def setUp(self) -> None:
        reset_jobs()

    def tearDown(self) -> None:
        reset_jobs()

    def test_invalid_day_is_400(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            client = TestClient(create_app(store))
            response = client.get("/?day=not-a-date")
            self.assertEqual(response.status_code, 400)

    def test_summary_shows_sync_date_and_button(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            seed_list(store)
            ok_day(store, date(2026, 8, 30))
            client = TestClient(create_app(store))
            response = client.get("/?day=2026-08-30")
            self.assertEqual(response.status_code, 200)
            self.assertIn("题材进掉", response.text)
            self.assertNotIn("题材尽调", response.text)
            self.assertIn("当前同步：2026-08-30 榜 · 8月31日 15:30 采入", response.text)
            self.assertIn("同步榜单", response.text)
            self.assertIn('action="/sync"', response.text)

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
            self.assertIn("当前同步：还没有有效快照", response.text)

    def test_post_sync_redirects_to_job(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            app = create_app(store)

            def fake_runner(current_store) -> str | None:
                del current_store
                print("1_2_8 1", flush=True)
                return None

            app.state.crawl_runner = fake_runner
            client = TestClient(app)
            response = client.post("/sync", follow_redirects=False)
            self.assertEqual(response.status_code, 303)
            location = response.headers["location"]
            self.assertTrue(location.startswith("/sync/"))
            job_id = location.rsplit("/", 1)[-1]
            finished = wait_for_sync_job(job_id)
            self.assertEqual(finished.status, JOB_DONE)
            job_page = client.get(location)
            self.assertEqual(job_page.status_code, 200)
            self.assertIn("同步榜单", job_page.text)
            self.assertIn("1_2_8 1", job_page.text)
            self.assertNotIn('http-equiv="refresh"', job_page.text)

    def test_foreign_origin_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            client = TestClient(create_app(store))
            response = client.post(
                "/sync",
                headers={"origin": "https://evil.example"},
            )
            self.assertEqual(response.status_code, 403)

    def test_second_sync_rejected_while_running(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            started = threading.Event()
            release = threading.Event()

            def blocking_runner(current_store) -> str | None:
                del current_store
                started.set()
                release.wait(timeout=2)
                return None

            app = create_app(store)
            app.state.crawl_runner = blocking_runner
            client = TestClient(app)
            first = client.post("/sync", follow_redirects=False)
            self.assertEqual(first.status_code, 303)
            self.assertTrue(started.wait(timeout=2))
            try:
                second = client.post("/sync", follow_redirects=False)
                self.assertEqual(second.status_code, 400)
            finally:
                release.set()


def wait_for_sync_job(job_id: str, timeout: float = 2.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        job = get_job(job_id)
        if job is not None and job.status in {JOB_DONE, JOB_FAILED}:
            return job
        time.sleep(0.01)
    raise AssertionError("同步任务没有在时限内结束")


class SyncStatusFormatTest(unittest.TestCase):
    def test_format_sync_status(self) -> None:
        self.assertEqual(
            format_sync_status(date(2026, 8, 30), datetime(2026, 8, 31, 15, 30)),
            "2026-08-30 榜 · 8月31日 15:30 采入",
        )


if __name__ == "__main__":
    unittest.main()
