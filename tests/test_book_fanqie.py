from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from book.fetch import FetchResult, PlatformHalted
from book.models import PLATFORM_FANQIE, SNAPSHOT_MISSING, SNAPSHOT_OK, RankList
from book.platforms.fanqie import (
    FanqieCrawler,
    font_url_from_html,
    snapshot_date_from_state,
)
from book.store import Store

SHANGHAI = timezone(timedelta(hours=8))

LIST = RankList(
    platform=PLATFORM_FANQIE,
    list_id="1_2_8",
    channel="male",
    rank_kind="read",
    category="都市日常",
    has_occupancy=False,
)

RANK_HTML = (
    'window.__INITIAL_STATE__={"rank":{"rankCategoryTypeList":{}}};'
    'src:url("https://cdn.fanqienovel.com/font.woff2")'
)


def fetch(url: str, text: str = "", status: int = 200) -> FetchResult:
    return FetchResult(
        url=url,
        status_code=status,
        content=text.encode("utf-8"),
        text=text,
        content_type="application/json",
    )


class FakeClient:
    def __init__(self, responses: dict[str, FetchResult | Exception]) -> None:
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url: str, referer: str | None = None, headers: dict | None = None) -> FetchResult:
        del referer, headers
        self.urls.append(url)
        for prefix, result in self.responses.items():
            if prefix in url or url == prefix:
                if isinstance(result, Exception):
                    raise result
                return result
        raise AssertionError(f"unexpected url {url}")

    def close(self) -> None:
        return


def book_json(books: list[dict] | None = None, code: int = 0) -> str:
    return json.dumps({"code": code, "data": {"book_list": books or []}})


class FontUrlTest(unittest.TestCase):
    def test_reads_quoted_and_plain_src_url(self) -> None:
        self.assertEqual(
            font_url_from_html('src:url("https://cdn.fanqienovel.com/a.woff2")'),
            "https://cdn.fanqienovel.com/a.woff2",
        )
        self.assertEqual(
            font_url_from_html("src:url(https://cdn.fanqienovel.com/a.woff2)"),
            "https://cdn.fanqienovel.com/a.woff2",
        )
        self.assertEqual(
            font_url_from_html("src: url('https://cdn.fanqienovel.com/a.woff2')"),
            "https://cdn.fanqienovel.com/a.woff2",
        )


class SnapshotDateTest(unittest.TestCase):
    def test_after_three_uses_yesterday(self) -> None:
        captured = datetime(2026, 8, 31, 15, 30, tzinfo=SHANGHAI)
        self.assertEqual(snapshot_date_from_state({}, captured).isoformat(), "2026-08-30")

    def test_before_three_uses_day_before_yesterday(self) -> None:
        captured = datetime(2026, 8, 31, 10, 0, tzinfo=SHANGHAI)
        self.assertEqual(snapshot_date_from_state({}, captured).isoformat(), "2026-08-29")


class FanqieCrawlTest(unittest.TestCase):
    def test_rank_page_halt_records_and_marks_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            client = FakeClient(
                {"https://fanqienovel.com/rank": PlatformHalted(PLATFORM_FANQIE, "HTTP 403")}
            )
            crawler = FanqieCrawler(store, client=client)
            with patch("book.platforms.fanqie.load_catalog", return_value=[LIST]), patch(
                "book.platforms.fanqie.save_catalog"
            ), patch("book.platforms.fanqie.mapping_from_woff", return_value={}):
                halted = crawler.crawl()
            self.assertEqual(halted, "HTTP 403")
            self.assertIsNotNone(store.get_halt(PLATFORM_FANQIE))
            day = store.snapshot_dates()[0]
            snapshot = store.get_snapshot(PLATFORM_FANQIE, "1_2_8", day)
            self.assertEqual(snapshot.status, SNAPSHOT_MISSING)

    def test_empty_book_list_is_missing_not_ok(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            client = FakeClient(
                {
                    "https://fanqienovel.com/rank": fetch("https://fanqienovel.com/rank", RANK_HTML),
                    "font.woff2": fetch("https://cdn.fanqienovel.com/font.woff2", "font"),
                    "/api/rank/category/list": fetch(
                        "api", book_json([]), status=200
                    ),
                }
            )
            crawler = FanqieCrawler(store, client=client)
            captured = datetime(2026, 8, 31, 15, 30, tzinfo=SHANGHAI)
            with patch("book.platforms.fanqie.load_catalog", return_value=[LIST]), patch(
                "book.platforms.fanqie.save_catalog"
            ), patch("book.platforms.fanqie.mapping_from_woff", return_value={}), patch(
                "book.platforms.fanqie.datetime"
            ) as mocked:
                mocked.now.return_value = captured
                mocked.fromtimestamp = datetime.fromtimestamp
                crawler.crawl()
            from datetime import date as date_cls

            loaded = store.get_snapshot(PLATFORM_FANQIE, "1_2_8", date_cls(2026, 8, 30))
            self.assertEqual(loaded.status, SNAPSHOT_MISSING)
            self.assertEqual(loaded.entries, ())

    def test_html_challenge_halts_remaining_lists(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            second = RankList(
                platform=PLATFORM_FANQIE,
                list_id="1_2_9",
                channel="male",
                rank_kind="read",
                category="玄幻",
            )
            client = FakeClient(
                {
                    "https://fanqienovel.com/rank": fetch("https://fanqienovel.com/rank", RANK_HTML),
                    "font.woff2": fetch("https://cdn.fanqienovel.com/font.woff2", "font"),
                    "/api/rank/category/list": fetch("api", "<html>安全验证</html>"),
                }
            )
            crawler = FanqieCrawler(store, client=client)
            captured = datetime(2026, 8, 31, 15, 30, tzinfo=SHANGHAI)
            with patch("book.platforms.fanqie.load_catalog", return_value=[LIST, second]), patch(
                "book.platforms.fanqie.save_catalog"
            ), patch("book.platforms.fanqie.mapping_from_woff", return_value={}), patch(
                "book.platforms.fanqie.datetime"
            ) as mocked:
                mocked.now.return_value = captured
                mocked.fromtimestamp = datetime.fromtimestamp
                halted = crawler.crawl()
            from datetime import date as date_cls

            self.assertIsNotNone(halted)
            self.assertIsNotNone(store.get_halt(PLATFORM_FANQIE))
            self.assertEqual(
                store.get_snapshot(PLATFORM_FANQIE, "1_2_8", date_cls(2026, 8, 30)).status,
                SNAPSHOT_MISSING,
            )
            self.assertEqual(
                store.get_snapshot(PLATFORM_FANQIE, "1_2_9", date_cls(2026, 8, 30)).status,
                SNAPSHOT_MISSING,
            )

    def test_ok_list_then_empty_keeps_ok_on_retry_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = Store(Path(temp_dir) / "windvane.sqlite")
            payload = book_json(
                [
                    {
                        "currentPos": 1,
                        "bookId": "99",
                        "bookName": "甲",
                        "author": "乙",
                        "creationStatus": "1",
                        "read_count": 12,
                    }
                ]
            )
            client = FakeClient(
                {
                    "https://fanqienovel.com/rank": fetch("https://fanqienovel.com/rank", RANK_HTML),
                    "font.woff2": fetch("https://cdn.fanqienovel.com/font.woff2", "font"),
                    "/api/rank/category/list": fetch("api", payload),
                }
            )
            crawler = FanqieCrawler(store, client=client)
            captured = datetime(2026, 8, 31, 15, 30, tzinfo=SHANGHAI)
            with patch("book.platforms.fanqie.load_catalog", return_value=[LIST]), patch(
                "book.platforms.fanqie.save_catalog"
            ), patch("book.platforms.fanqie.mapping_from_woff", return_value={}), patch(
                "book.platforms.fanqie.datetime"
            ) as mocked:
                mocked.now.return_value = captured
                mocked.fromtimestamp = datetime.fromtimestamp
                crawler.crawl()
                client.responses["/api/rank/category/list"] = PlatformHalted(
                    PLATFORM_FANQIE, "HTTP 403 https://fanqienovel.com/api"
                )
                crawler.crawl()
            from datetime import date

            loaded = store.get_snapshot(PLATFORM_FANQIE, "1_2_8", date(2026, 8, 30))
            self.assertEqual(loaded.status, SNAPSHOT_OK)
            self.assertEqual(loaded.entries[0].work_id, "99")


if __name__ == "__main__":
    unittest.main()
