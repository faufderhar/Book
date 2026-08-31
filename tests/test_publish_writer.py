from __future__ import annotations

import unittest
from pathlib import Path

from publish.manuscript import BookProfile
from publish.writer import (
    BOOK_MANAGE_URL,
    LOGGED_IN_HINTS,
    WRITER_HOMES,
    PublishHalt,
    click_first_visible_name,
    collect_search_hits,
    create_chapter_href,
    extract_book_id,
    extract_chapter_id,
    fill_chapter_title,
    open_writer_home,
    open_search_hit,
    split_schedule_stamp,
    unique_remote_chapters,
    wait_for_chapter_editor,
    wait_until_logged_in,
)
from publish.plan import RemoteChapter, SearchHit


class FakeLocator:
    def __init__(self, *, visible: bool = False, children: dict[str, "FakeLocator"] | None = None) -> None:
        self.visible = visible
        self.clicks = 0
        self.children = children or {}
        self.filled: list[str] = []

    def count(self) -> int:
        return 1 if self.visible else 0

    @property
    def first(self) -> FakeLocator:
        return self

    def is_visible(self) -> bool:
        return self.visible

    def is_enabled(self) -> bool:
        return self.visible

    def click(self) -> None:
        self.clicks += 1

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        del exact
        return self.children.get(text, FakeLocator())

    def fill(self, value: str) -> None:
        self.filled.append(value)

    def wait_for(self, state: str = "visible", timeout: int = 15000) -> None:
        del state, timeout
        if not self.visible:
            raise RuntimeError("not visible")


class FakePage:
    def __init__(
        self,
        *,
        visible_texts: tuple[str, ...] = (),
        fail_first_goto: bool = False,
        evaluate_result: object | None = None,
        locators: dict[str, FakeLocator] | None = None,
        placeholders: dict[str, FakeLocator] | None = None,
    ) -> None:
        self.visible_texts = visible_texts
        self.fail_first_goto = fail_first_goto
        self.gotos: list[str] = []
        self.timeouts = 0
        self.evaluate_result = evaluate_result if evaluate_result is not None else []
        self.locators = locators or {}
        self.placeholders = placeholders or {}

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.gotos.append(url)
        if self.fail_first_goto and len(self.gotos) == 1:
            raise RuntimeError("first home down")

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        return FakeLocator(visible=any(text in item for item in self.visible_texts))

    def get_by_role(self, role: str, name: str | None = None) -> FakeLocator:
        return FakeLocator(visible=name in self.visible_texts)

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.timeouts += 1

    def wait_for_selector(self, selector: str, timeout: int = 8000) -> None:
        del selector, timeout

    def wait_for_url(self, url: object, timeout: int = 15000) -> None:
        del url, timeout

    def evaluate(self, script: str, *args: object) -> object:
        del script, args
        return self.evaluate_result

    def locator(self, selector: str) -> FakeLocator:
        return self.locators.get(selector, FakeLocator())

    def get_by_placeholder(self, pattern: object, exact: bool = False) -> FakeLocator:
        del exact
        needle = pattern.pattern if hasattr(pattern, "pattern") else str(pattern)
        for placeholder, locator in self.placeholders.items():
            if needle in placeholder:
                return locator
        return FakeLocator()


class OpenWriterHomeTest(unittest.TestCase):
    def test_opens_first_home_and_waits_until_logged_in(self) -> None:
        page = FakePage(visible_texts=LOGGED_IN_HINTS)
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=1)
        open_writer_home(page, profile)
        self.assertEqual(page.gotos, [WRITER_HOMES[0]])
        self.assertEqual(WRITER_HOMES[0], BOOK_MANAGE_URL)

    def test_falls_back_to_next_home_when_first_goto_fails(self) -> None:
        page = FakePage(visible_texts=LOGGED_IN_HINTS, fail_first_goto=True)
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=1)
        open_writer_home(page, profile)
        self.assertEqual(page.gotos[:2], [WRITER_HOMES[0], WRITER_HOMES[1]])

    def test_login_timeout_raises_publish_halt(self) -> None:
        page = FakePage()
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=0)
        with self.assertRaises(PublishHalt):
            wait_until_logged_in(page, profile)

    def test_click_first_visible_name_clicks_matching_text(self) -> None:
        page = FakePage(visible_texts=("作品管理",))
        self.assertTrue(click_first_visible_name(page, ("作品管理", "作品列表")))


class BookManageSearchTest(unittest.TestCase):
    def test_collect_search_hits_opens_book_manage_and_reads_cards(self) -> None:
        page = FakePage(
            visible_texts=LOGGED_IN_HINTS,
            evaluate_result=[
                {
                    "book_id": "7679308798468557886",
                    "work_name": "婚约不许我升职",
                    "text": "婚约不许我升职",
                }
            ],
        )
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=1)
        hits = collect_search_hits(page, profile)
        self.assertEqual(page.gotos, [BOOK_MANAGE_URL])
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].book_id, "7679308798468557886")
        self.assertEqual(hits[0].work_name, "婚约不许我升职")
        self.assertEqual(hits[0].row_text, "婚约不许我升职")

    def test_open_search_hit_clicks_chapter_manage_on_card(self) -> None:
        chapter_button = FakeLocator(visible=True)
        card = FakeLocator(
            visible=True,
            children={"章节管理": chapter_button, "作品设置": FakeLocator()},
        )
        page = FakePage(locators={"#long-article-table-item-7679308798468557886": card})
        hit = SearchHit(
            book_id="7679308798468557886",
            row_text="婚约不许我升职",
            work_name="婚约不许我升职",
        )
        self.assertTrue(open_search_hit(page, hit))
        self.assertEqual(chapter_button.clicks, 1)

    def test_extract_book_id_from_writer_paths(self) -> None:
        chapter_manage = (
            "https://fanqienovel.com/main/writer/chapter-manage/"
            "7679308798468557886&%E5%A9%9A%E7%BA%A6?type=1"
        )
        book_info = "https://fanqienovel.com/main/writer/book-info/7679308798468557886?type=2"
        publish = "https://fanqienovel.com/main/writer/7679308798468557886/publish/?enter_from=newchapter_1"
        self.assertEqual(extract_book_id(chapter_manage), "7679308798468557886")
        self.assertEqual(extract_book_id(book_info), "7679308798468557886")
        self.assertEqual(extract_book_id(publish), "7679308798468557886")


class ChapterEditorTest(unittest.TestCase):
    def test_create_chapter_href_prefers_newchapter_link(self) -> None:
        page = FakePage(
            evaluate_result=[
                "https://fanqienovel.com/main/writer/1/publish/99/?enter_from=modifychapter",
                "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter",
            ]
        )
        self.assertEqual(
            create_chapter_href(page),
            "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter",
        )

    def test_fill_chapter_title_uses_placeholder(self) -> None:
        title_box = FakeLocator(visible=True)
        page = FakePage(placeholders={"请输入标题": title_box})
        fill_chapter_title(page, "登记才算")
        self.assertEqual(title_box.filled, ["登记才算"])

    def test_wait_for_chapter_editor_halts_without_title_box(self) -> None:
        page = FakePage()
        with self.assertRaises(PublishHalt):
            wait_for_chapter_editor(page)

    def test_extract_chapter_id_from_publish_path(self) -> None:
        url = (
            "https://fanqienovel.com/main/writer/7679308798468557886/"
            "publish/7680016006521029182?enter_from=newchapter_1"
        )
        self.assertEqual(extract_chapter_id(url), "7680016006521029182")


    def test_unique_remote_chapters_keeps_shortest_title_per_sequence(self) -> None:
        remotes = unique_remote_chapters(
            [
                RemoteChapter(title="第6章 婚约原件 3828 0", published=True),
                RemoteChapter(title="第6章 婚约原件", published=True, chapter_id="99"),
                RemoteChapter(title="第5章 走廊上的签名", published=True),
            ]
        )
        self.assertEqual(len(remotes), 2)
        self.assertEqual(remotes[0].title, "第5章 走廊上的签名")
        self.assertEqual(remotes[1].title, "第6章 婚约原件")
        self.assertEqual(remotes[1].chapter_id, "99")

    def test_unique_remote_chapters_prefers_published_row(self) -> None:
        remotes = unique_remote_chapters(
            [
                RemoteChapter(title="第1章 工牌0727", published=False),
                RemoteChapter(title="第1章 工牌0727 已发布", published=True, chapter_id="1"),
            ]
        )
        self.assertEqual(len(remotes), 1)
        self.assertTrue(remotes[0].published)
        self.assertEqual(remotes[0].chapter_id, "1")

class ScheduleStampTest(unittest.TestCase):
    def test_splits_date_and_time(self) -> None:
        self.assertEqual(split_schedule_stamp("2026-09-01 08:00"), ("2026-09-01", "08:00"))
        with self.assertRaises(PublishHalt):
            split_schedule_stamp("08:00")


if __name__ == "__main__":
    unittest.main()
