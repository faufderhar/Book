from __future__ import annotations

import unittest
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory

from publish.manuscript import BookProfile
from publish.manuscript import Chapter
from publish.manuscript import Manuscript
from publish.writer import (
    BOOK_MANAGE_URL,
    LOGGED_IN_HINTS,
    WRITER_HOMES,
    execute_chapter_actions,
    execute_planned_publish,
    open_create_chapter,
    run_publish,
    PublishHalt,
    PublishReport,
    apply_planned_settings,
    click_catalog_tab,
    click_first_visible_name,
    dismiss_popups,
    dismiss_tour_guide,
    chapter_catalog_url,
    collect_catalog_rows,
    collect_search_hits,
    create_chapter_href,
    enable_timed_publish,
    extract_book_id,
    extract_chapter_id,
    fill_chapter_title,
    list_remote_chapters,
    list_platform_books,
    open_bound_book,
    open_book_settings,
    open_remote_chapter,
    open_writer_home,
    open_search_hit,
    return_to_chapter_catalog,
    select_all_key,
    split_schedule_stamp,
    unique_remote_chapters,
    CATALOG_SCROLL_STEPS,
    CATALOG_SCROLL_IDLE_STEPS,
    CATALOG_SWITCH_POLLS,
    COLLECT_CHAPTER_ROWS_JS,
    catalog_row_status,
    compact_chapter_title,
    CREATE_CHAPTER_BUTTONS,
    CREATE_CHAPTER_MISSING,
    CREATE_CHAPTER_OK,
    CREATE_CHAPTER_POLLS,
    CREATE_CHAPTER_STUCK,
    scroll_and_collect,
    click_create_chapter_button,
    wait_for_catalog_rows,
    wait_for_catalog_switch,
    wait_for_chapter_editor,
    wait_until_logged_in,
    wait_until_publish_settings,
    same_tab_goto,
    write_chapter,
)
from publish.plan import (
    ACTION_CREATE_DRAFT,
    ACTION_UPDATE_VISIBILITY,
    CommandMode,
    ChapterAction,
    MODE_PUBLISH,
    PublishPlan,
    RemoteChapter,
    SearchHit,
)


class FakeLocator:
    def __init__(
        self,
        *,
        visible: bool = False,
        children: dict[str, "FakeLocator"] | None = None,
        inner_text: str = "",
    ) -> None:
        self.visible = visible
        self.clicks = 0
        self.children = children or {}
        self.filled: list[str] = []
        self._inner_text = inner_text

    def count(self) -> int:
        return 1 if self.visible else 0

    @property
    def first(self) -> FakeLocator:
        return self

    @property
    def last(self) -> FakeLocator:
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

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False) -> FakeLocator:
        del role, exact
        return self.children.get(name or "", FakeLocator())

    def locator(self, selector: str) -> FakeLocator:
        return self.children.get(selector, FakeLocator())

    def fill(self, value: str) -> None:
        self.filled.append(value)

    def get_attribute(self, name: str) -> str | None:
        del name
        return None

    def wait_for(self, state: str = "visible", timeout: int = 15000) -> None:
        del state, timeout
        if not self.visible:
            raise RuntimeError("not visible")

    def inner_text(self) -> str:
        return self._inner_text


class LateLocator(FakeLocator):
    """弹层已挂上、按钮过几轮才可点；点中后把弹层关掉，跟真页面一样。"""

    def __init__(self, *, ready_after: int, dialog: FakeLocator | None = None) -> None:
        super().__init__(visible=True)
        self.ready_after = ready_after
        self.dialog = dialog
        self.probes = 0

    def is_enabled(self) -> bool:
        self.probes += 1
        return self.probes > self.ready_after

    def click(self) -> None:
        super().click()
        if self.dialog is not None:
            self.dialog.visible = False


class FakeKeyboard:
    def __init__(self) -> None:
        self.pressed: list[str] = []
        self.inserted: list[str] = []

    def press(self, key: str) -> None:
        self.pressed.append(key)

    def insert_text(self, text: str) -> None:
        self.inserted.append(text)


class FakeMouse:
    def wheel(self, delta_x: int, delta_y: int) -> None:
        del delta_x, delta_y


class FakePage:
    def __init__(
        self,
        *,
        visible_texts: tuple[str, ...] = (),
        fail_first_goto: bool = False,
        evaluate_result: object | None = None,
        locators: dict[str, FakeLocator] | None = None,
        placeholders: dict[str, FakeLocator] | None = None,
        roles: dict[tuple[str, str], FakeLocator] | None = None,
        reveal_texts: tuple[str, ...] = (),
        reveal_after: int = 0,
        reveal_url: str = "",
        fail_goto_remaining: int = 0,
        arrive_on_failed_goto: bool = False,
    ) -> None:
        self.visible_texts = visible_texts
        self.fail_first_goto = fail_first_goto
        self.gotos: list[str] = []
        self.url = ""
        self.timeouts = 0
        self.evaluate_result = evaluate_result if evaluate_result is not None else []
        self.locators = locators or {}
        self.placeholders = placeholders or {}
        self.roles = roles or {}
        self.reveal_texts = reveal_texts
        self.reveal_after = reveal_after
        self.reveal_url = reveal_url
        self.keyboard = FakeKeyboard()
        self.mouse = FakeMouse()
        self.fail_goto_remaining = fail_goto_remaining
        self.arrive_on_failed_goto = arrive_on_failed_goto

    def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int | None = None) -> None:
        del wait_until, timeout
        self.gotos.append(url)
        if self.fail_goto_remaining > 0:
            self.fail_goto_remaining -= 1
            if self.arrive_on_failed_goto:
                self.url = url
            raise RuntimeError("Page.goto: Timeout 15000ms exceeded")
        if self.fail_first_goto and len(self.gotos) == 1:
            self.url = url
            raise RuntimeError("first home down")
        self.url = url

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        del exact
        if hasattr(text, "search"):
            return FakeLocator(visible=any(bool(text.search(item)) for item in self.visible_texts))
        return FakeLocator(visible=any(text in item for item in self.visible_texts))

    def get_by_role(self, role: str, name: str | None = None, exact: bool = False) -> FakeLocator:
        del exact
        keyed = self.roles.get((role, name or ""))
        if keyed is not None:
            return keyed
        return FakeLocator(visible=name in self.visible_texts)

    def get_by_label(self, text: str, exact: bool = False) -> FakeLocator:
        del text, exact
        return FakeLocator()

    def set_default_timeout(self, milliseconds: int) -> None:
        self.default_timeout = milliseconds

    def set_default_navigation_timeout(self, milliseconds: int) -> None:
        self.default_navigation_timeout = milliseconds

    def wait_for_timeout(self, milliseconds: int) -> None:
        self.timeouts += 1
        if self.reveal_after and self.timeouts >= self.reveal_after:
            if self.reveal_texts:
                self.visible_texts = self.reveal_texts
            if self.reveal_url:
                self.url = self.reveal_url

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

    def test_already_logged_in_succeeds_with_zero_wait(self) -> None:
        page = FakePage(visible_texts=LOGGED_IN_HINTS)
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=0)
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

    def test_collect_search_hits_fills_search_box(self) -> None:
        search_box = FakeLocator(visible=True)
        page = FakePage(
            visible_texts=LOGGED_IN_HINTS,
            placeholders={"搜索作品": search_box},
            evaluate_result=[],
        )
        profile = BookProfile(
            path=Path("书资料.yml"),
            human_wait_seconds=1,
            fields={"作品名称": "婚约不许我升职"},
        )
        collect_search_hits(page, profile)
        self.assertEqual(search_box.filled, ["婚约不许我升职"])
        self.assertEqual(page.keyboard.pressed, ["Enter"])

    def test_list_platform_books_does_not_fill_search(self) -> None:
        search_box = FakeLocator(visible=True)
        page = FakePage(
            visible_texts=LOGGED_IN_HINTS,
            placeholders={"搜索作品": search_box},
            evaluate_result=[
                {
                    "book_id": "1",
                    "work_name": "婚约不许我升职",
                    "text": "婚约不许我升职",
                },
                {"book_id": "2", "work_name": "另一本", "text": "另一本"},
            ],
        )
        profile = BookProfile(
            path=Path("书资料.yml"),
            human_wait_seconds=1,
            fields={"作品名称": "工牌不认婚约"},
        )
        hits = list_platform_books(page, profile)
        self.assertEqual(search_box.filled, [])
        self.assertEqual(page.gotos, [BOOK_MANAGE_URL])
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].work_name, "婚约不许我升职")
        self.assertEqual(hits[1].book_id, "2")

    def test_bound_book_does_not_open_same_title_other_id(self) -> None:
        chapter_button = FakeLocator(visible=True)
        other_card = FakeLocator(
            visible=True,
            children={"章节管理": chapter_button, "作品设置": FakeLocator()},
        )
        page = FakePage(
            visible_texts=LOGGED_IN_HINTS,
            evaluate_result=[
                {
                    "book_id": "20002",
                    "work_name": "婚约不许我升职",
                    "text": "婚约不许我升职",
                }
            ],
            locators={"#long-article-table-item-20002": other_card},
        )
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=1, book_id="10001")
        self.assertFalse(open_bound_book(page, "10001", profile))
        self.assertEqual(chapter_button.clicks, 0)

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

    def test_open_book_settings_goes_to_book_info_url(self) -> None:
        page = FakePage()
        self.assertTrue(open_book_settings(page, "7679308798468557886"))
        self.assertEqual(
            page.gotos,
            ["https://fanqienovel.com/main/writer/book-info/7679308798468557886?type=2"],
        )

    def test_apply_planned_settings_opens_book_info_without_nav_buttons(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            profile_path = root / "书资料.yml"
            profile_path.write_text("绑定:\n  作品ID: '7679308798468557886'\n", encoding="utf-8")
            profile = BookProfile(
                path=profile_path,
                book_id="7679308798468557886",
                fields={"简介": "用工牌打穿婚书"},
            )
            manuscript = Manuscript(directory=root, profile=profile, chapters=())
            plan = PublishPlan(book_id=profile.book_id, fields_to_write={"简介": "用工牌打穿婚书"})
            report = PublishReport()
            page = FakePage()
            apply_planned_settings(page, manuscript, plan, report, creating=False)
            self.assertEqual(
                page.gotos,
                ["https://fanqienovel.com/main/writer/book-info/7679308798468557886?type=2"],
            )

    def test_open_book_settings_without_book_id_requires_visible_entry(self) -> None:
        page = FakePage()
        self.assertFalse(open_book_settings(page, ""))
        page_with_entry = FakePage(visible_texts=("作品信息",))
        self.assertTrue(open_book_settings(page_with_entry, ""))


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

    def test_open_create_chapter_goes_to_constructed_url(self) -> None:
        page = FakePage()
        open_create_chapter(page, "10001")
        self.assertEqual(
            page.gotos,
            ["https://fanqienovel.com/main/writer/10001/publish/?enter_from=newchapter"],
        )

    def test_open_create_chapter_clicks_button_when_goto_times_out(self) -> None:
        page = FakePage(fail_goto_remaining=2)

        class LandingButton(FakeLocator):
            def click(self) -> None:
                super().click()
                page.url = "https://fanqienovel.com/main/writer/10001/publish/?enter_from=newchapter"

        create_button = LandingButton(visible=True)
        page.roles[("button", "创建章节")] = create_button
        open_create_chapter(page, "10001")
        self.assertEqual(create_button.clicks, 1)
        self.assertEqual(len(page.gotos), 2)

    def test_fill_chapter_title_uses_placeholder(self) -> None:
        title_box = FakeLocator(visible=True)
        page = FakePage(placeholders={"请输入标题": title_box})
        fill_chapter_title(page, "登记才算")
        self.assertEqual(title_box.filled, ["登记才算"])

    def test_wait_for_chapter_editor_halts_without_title_box(self) -> None:
        page = FakePage()
        with self.assertRaises(PublishHalt):
            wait_for_chapter_editor(page)

    def test_wait_for_chapter_editor_dismisses_schedule_notice(self) -> None:
        know = FakeLocator(visible=True)
        title_box = FakeLocator(visible=True)
        page = FakePage(
            roles={("button", "我知道了"): know},
            placeholders={"请输入标题": title_box},
        )
        wait_for_chapter_editor(page)
        self.assertEqual(know.clicks, 1)

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

    def test_unique_remote_chapters_keeps_chapter_id_over_shorter_title(self) -> None:
        remotes = unique_remote_chapters(
            [
                RemoteChapter(title="第8章 投促局口径", published=False, visibility="定时发布"),
                RemoteChapter(
                    title="第8章 投促局口径 定时发布 3828字",
                    published=False,
                    visibility="定时发布",
                    chapter_id="88",
                ),
            ]
        )
        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0].chapter_id, "88")


@patch("publish.writer.CATALOG_TAB_WAIT_SECONDS", 0)
class ChapterCatalogNavigationTest(unittest.TestCase):
    def test_catalog_url_matches_book_info_type_pattern(self) -> None:
        self.assertEqual(
            chapter_catalog_url("7679308798468557886"),
            "https://fanqienovel.com/main/writer/chapter-manage/7679308798468557886?type=1",
        )

    def test_return_to_chapter_catalog_uses_type_query(self) -> None:
        page = FakePage()
        return_to_chapter_catalog(page, "7679308798468557886")
        self.assertEqual(page.gotos, [chapter_catalog_url("7679308798468557886")])

    def test_list_remote_chapters_opens_catalog_url_not_book_list(self) -> None:
        tab = FakeLocator(visible=True)
        drafts = FakeLocator(visible=True)
        page = FakePage(roles={("tab", "章节管理"): tab, ("tab", "草稿箱"): drafts})
        list_remote_chapters(page, "7679308798468557886")
        self.assertEqual(page.gotos, [chapter_catalog_url("7679308798468557886")])
        self.assertGreaterEqual(tab.clicks, 1)
        self.assertGreaterEqual(drafts.clicks, 1)

    def test_list_remote_chapters_halts_when_catalog_tab_missing(self) -> None:
        page = FakePage()
        with self.assertRaises(PublishHalt) as raised:
            list_remote_chapters(page, "7679308798468557886")
        self.assertIn("未确认水位", str(raised.exception))
        self.assertEqual(page.gotos, [chapter_catalog_url("7679308798468557886")])

    def test_list_remote_chapters_halts_when_draft_box_missing(self) -> None:
        """草稿箱点不开不能静默当成「没有草稿」，否则改可见性的动作会全部落空。"""
        page = FakePage()
        opened: list[tuple[str, ...]] = []

        def only_catalog(page_arg, names, seconds=None):
            del page_arg, seconds
            opened.append(names)
            return names != ("草稿箱",)

        with patch("publish.writer.click_catalog_tab", side_effect=only_catalog):
            with self.assertRaises(PublishHalt) as raised:
                list_remote_chapters(page, "7679308798468557886")
        self.assertIn("草稿箱", str(raised.exception))
        self.assertIn("未确认水位", str(raised.exception))
        self.assertIn(("草稿箱",), opened)

    def test_list_remote_chapters_merges_both_tabs(self) -> None:
        page = FakePage()
        pages = {
            "章节管理": [RemoteChapter(title="第1章 甲", chapter_id="c1")],
            "草稿箱": [
                RemoteChapter(title="第1章 甲", chapter_id="c1"),
                RemoteChapter(title="第2章 乙", chapter_id="c2"),
            ],
        }
        current = {"tab": ""}

        def track(page_arg, names, seconds=None):
            del page_arg, seconds
            current["tab"] = names[0]
            return True

        def rows(page_arg, **kwargs):
            del page_arg, kwargs
            return list(pages.get(current["tab"], []))

        with (
            patch("publish.writer.click_catalog_tab", side_effect=track),
            patch("publish.writer.collect_catalog_rows", side_effect=rows),
        ):
            remotes = list_remote_chapters(page, "7679308798468557886")
        self.assertEqual([item.chapter_id for item in remotes], ["c1", "c2"])

    def test_click_catalog_tab_ignores_plain_text_on_book_list(self) -> None:
        page = FakePage(
            visible_texts=("全部", "章节管理"),
            roles={("tab", "全部"): FakeLocator()},
        )
        self.assertFalse(click_catalog_tab(page, ("全部",)))

    def test_click_catalog_tab_clicks_chapter_manage(self) -> None:
        tab = FakeLocator(visible=True)
        page = FakePage(roles={("tab", "章节管理"): tab})
        self.assertTrue(click_catalog_tab(page, ("章节管理",)))
        self.assertEqual(tab.clicks, 1)

    def test_open_remote_chapter_goes_to_editor_url_when_id_known(self) -> None:
        page = FakePage()
        remote = RemoteChapter(title="第8章 投促局口径", chapter_id="88", visibility="草稿")
        open_remote_chapter(page, remote, "7679308798468557886")
        self.assertEqual(
            page.gotos,
            [
                "https://fanqienovel.com/main/writer/7679308798468557886/"
                "publish/88/?enter_from=modifydraft"
            ],
        )

    def test_open_remote_chapter_without_id_returns_to_catalog_then_halts(self) -> None:
        page = FakePage()
        remote = RemoteChapter(title="第8章 投促局口径", visibility="草稿")
        with self.assertRaises(PublishHalt) as raised:
            open_remote_chapter(page, remote, "7679308798468557886")
        self.assertIn("打不开远端章节", str(raised.exception))
        self.assertEqual(page.gotos, [chapter_catalog_url("7679308798468557886")])


class SameTabGotoTest(unittest.TestCase):
    def test_retries_then_succeeds(self) -> None:
        page = FakePage(fail_goto_remaining=1)
        same_tab_goto(page, "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter")
        self.assertEqual(
            page.gotos,
            [
                "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter",
                "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter",
            ],
        )
        self.assertEqual(
            page.url,
            "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter",
        )

    def test_timeout_after_arrival_still_succeeds(self) -> None:
        page = FakePage(fail_goto_remaining=1, arrive_on_failed_goto=True)
        same_tab_goto(page, "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter")
        self.assertEqual(len(page.gotos), 1)

    def test_unreached_timeout_becomes_publish_halt(self) -> None:
        page = FakePage(fail_goto_remaining=2)
        with self.assertRaises(PublishHalt) as raised:
            same_tab_goto(page, "https://fanqienovel.com/main/writer/1/publish/?enter_from=newchapter")
        self.assertIn("打不开页面", str(raised.exception))


class BoundBookOpenOnceTest(unittest.TestCase):
    def test_already_opened_bound_book_is_not_opened_again(self) -> None:
        manuscript = Manuscript(
            directory=Path("."),
            profile=BookProfile(
                path=Path("书资料.yml"),
                book_id="10001",
                fields={"作品名称": "甲"},
            ),
            chapters=(),
        )
        claim_plan = PublishPlan(book_id="10001")
        full_plan = PublishPlan(book_id="10001")
        with (
            patch("publish.writer.observe_claim_state", return_value=((), True)),
            patch("publish.writer.plan_publish", side_effect=[claim_plan, full_plan]),
            patch("publish.writer.save_profile"),
            patch("publish.writer.open_claimed_book") as opener,
            patch("publish.writer.list_remote_chapters", return_value=[]),
            patch("publish.writer.execute_chapter_actions"),
        ):
            execute_planned_publish(
                FakePage(),
                manuscript,
                CommandMode(MODE_PUBLISH),
                PublishReport(),
            )
        opener.assert_not_called()

    def test_failed_bound_open_retries_claim(self) -> None:
        manuscript = Manuscript(
            directory=Path("."),
            profile=BookProfile(
                path=Path("书资料.yml"),
                book_id="10001",
                fields={"作品名称": "甲"},
            ),
            chapters=(),
        )
        claim_plan = PublishPlan(book_id="10001")
        full_plan = PublishPlan(book_id="10001")
        with (
            patch("publish.writer.observe_claim_state", return_value=((), False)),
            patch("publish.writer.plan_publish", side_effect=[claim_plan, full_plan]),
            patch("publish.writer.save_profile"),
            patch("publish.writer.open_claimed_book", return_value=True) as opener,
            patch("publish.writer.list_remote_chapters", return_value=[]),
            patch("publish.writer.execute_chapter_actions"),
        ):
            execute_planned_publish(
                FakePage(),
                manuscript,
                CommandMode(MODE_PUBLISH),
                PublishReport(),
            )
        opener.assert_called_once()


class PublishSettingsWaitTest(unittest.TestCase):
    def test_chapter_body_typo_word_does_not_halt(self) -> None:
        page = FakePage(
            visible_texts=("第一篇的错别字组合",),
            reveal_texts=("发布设置",),
            reveal_after=1,
        )
        wait_until_publish_settings(page)

    def test_typo_dialog_clicks_ok_then_reaches_settings(self) -> None:
        confirm = FakeLocator(visible=True)
        dialog = FakeLocator(
            visible=True,
            inner_text="发布提示\n检测到你还有错别字未修改，是否确定提交？",
            children={"button.arco-btn-primary": confirm, "提交": confirm},
        )
        page = FakePage(
            locators={".auto-editor-error-modal": dialog, "[role='dialog']": dialog},
            reveal_texts=("发布设置",),
            reveal_after=1,
        )
        wait_until_publish_settings(page)
        self.assertEqual(confirm.clicks, 1)

    def test_typo_dialog_clicks_ok_when_submit_missing(self) -> None:
        confirm = FakeLocator(visible=True)
        dialog = FakeLocator(
            visible=True,
            inner_text="发布提示\n是否确定提交？",
            children={"确定": confirm},
        )
        page = FakePage(
            locators={"[role='dialog']": dialog},
            reveal_texts=("发布设置",),
            reveal_after=1,
        )
        wait_until_publish_settings(page)
        self.assertEqual(confirm.clicks, 1)

    def test_schedule_notice_is_dismissed_then_reaches_settings(self) -> None:
        know = FakeLocator(visible=True)
        page = FakePage(
            roles={("button", "我知道了"): know},
            visible_texts=("请在发布时间前30分钟提交修改内容，否则无法完成修改",),
            reveal_texts=("发布设置",),
            reveal_after=1,
        )
        wait_until_publish_settings(page)
        self.assertEqual(know.clicks, 1)

    def test_schedule_notice_overlay_text_is_dismissed(self) -> None:
        know = FakeLocator(visible=True)
        dialog = FakeLocator(
            visible=True,
            inner_text="提示\n请在发布时间前30分钟提交修改内容，否则无法完成修改",
            children={"我知道了": know},
        )
        page = FakePage(
            locators={"[role='dialog']": dialog},
            reveal_texts=("发布设置",),
            reveal_after=1,
        )
        wait_until_publish_settings(page)
        self.assertEqual(know.clicks, 1)

    def test_typo_dialog_button_enabled_late_is_retried(self) -> None:
        """弹层文字先挂上、按钮晚一拍才可点，不该一次没点着就停手。"""
        dialog = FakeLocator(
            visible=True,
            inner_text="发布提示\n检测到你还有错别字未修改，是否确定提交？",
        )
        confirm = LateLocator(ready_after=2, dialog=dialog)
        dialog.children = {"button.arco-btn-primary": confirm}
        page = FakePage(
            locators={"[role='dialog']": dialog},
            reveal_texts=("发布设置",),
            reveal_after=4,
        )
        wait_until_publish_settings(page)
        self.assertEqual(confirm.clicks, 1)
        self.assertGreater(confirm.probes, 2)

    def test_typo_dialog_falls_back_to_overlay_primary_button(self) -> None:
        """按钮叫什么不认识时，退到弹层自己的主按钮。"""
        primary = FakeLocator(visible=True)
        dialog = FakeLocator(
            visible=True,
            inner_text="发布提示\n是否确定提交？",
            children={"button.arco-btn-primary": primary},
        )
        page = FakePage(
            locators={".arco-modal": dialog},
            reveal_texts=("发布设置",),
            reveal_after=1,
        )
        wait_until_publish_settings(page)
        self.assertEqual(primary.clicks, 1)

    def test_typo_dialog_that_never_clears_waits_for_the_human(self) -> None:
        """自动点不掉就把浏览器留给人，人点掉了这一轮照常继续。"""
        dialog = FakeLocator(
            visible=True,
            inner_text="发布提示 检测到你还有错别字未修改，是否确定提交？",
        )
        page = FakePage(locators={"[role='dialog']": dialog})
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=120)
        with patch("publish.writer.advance_to_publish_settings", side_effect=["错别字提示", None]) as waited:
            wait_until_publish_settings(page, profile)
        self.assertEqual(waited.call_count, 2)

    def test_typo_dialog_halt_names_what_blocked(self) -> None:
        dialog = FakeLocator(
            visible=True,
            inner_text="发布提示 检测到你还有错别字未修改，是否确定提交？",
        )
        page = FakePage(locators={"[role='dialog']": dialog})
        profile = BookProfile(path=Path("书资料.yml"), human_wait_seconds=0)
        with patch("publish.writer.advance_to_publish_settings", return_value="发布提示 错别字未修改"):
            with self.assertRaises(PublishHalt) as raised:
                wait_until_publish_settings(page, profile)
        self.assertIn("错别字未修改", str(raised.exception))


    def test_review_choice_modal_takes_basic_check_not_the_quota_one(self) -> None:
        """「请选择内容检测方式」只走不限次数的基础检测，不动每章限两次的全面检测。"""
        full = FakeLocator(visible=True)
        modal = FakeLocator(
            visible=True,
            inner_text=(
                "请选择内容检测方式 全面检测（本章节剩余次数：2/2次） 将对章节内容进行深度排查；"
                " 基础检测（不限次数） 使用平台常规功能排查特定范围的违规内容 仅基础检测 全面检测"
            ),
        )
        basic = LateLocator(ready_after=0, dialog=modal)
        modal.children = {"仅基础检测": basic, "全面检测": full}
        page = FakePage(
            locators={".arco-modal": modal, "[role='dialog']": modal},
            reveal_texts=("发布设置",),
            reveal_after=2,
        )
        wait_until_publish_settings(page)
        self.assertEqual(basic.clicks, 1)
        self.assertEqual(full.clicks, 0)


class TourGuideTest(unittest.TestCase):
    def test_dismiss_popups_walks_the_tour_guide_away(self) -> None:
        """新手引导带遮罩会吃掉点击，必须先走完它。"""
        guide = FakeLocator(visible=True, inner_text="这里查看历史版本 2/3 下一步")
        step = LateLocator(ready_after=0, dialog=guide)
        guide.children = {"button.guide-card-footer-btn": step}
        page = FakePage(locators={".publish-tour-guide": guide})
        dismiss_popups(page)
        self.assertEqual(step.clicks, 1)
        self.assertFalse(guide.visible)

    def test_tour_guide_that_will_not_click_gets_removed(self) -> None:
        guide = FakeLocator(visible=True, inner_text="这里查看历史版本 2/3")
        page = FakePage(locators={".reactour__helper": guide}, evaluate_result=True)
        self.assertTrue(dismiss_tour_guide(page))


class CatalogTabWaitTest(unittest.TestCase):
    def test_catalog_tab_is_waited_for_not_judged_on_one_try(self) -> None:
        """目录页异步渲染，标签晚到不该被判成「打不开章节目录」。"""
        tab = FakeLocator(visible=False)
        page = FakePage(roles={("tab", "章节管理"): tab})

        calls = {"n": 0}
        original = page.wait_for_timeout

        def reveal(milliseconds: int) -> None:
            calls["n"] += 1
            if calls["n"] >= 3:
                tab.visible = True
            original(milliseconds)

        page.wait_for_timeout = reveal
        self.assertTrue(click_catalog_tab(page, ("章节管理",), seconds=5))
        self.assertEqual(tab.clicks, 1)


class LoginAnnounceTest(unittest.TestCase):
    def test_writer_homes_share_one_announce_set(self) -> None:
        """三个入口轮着试，「请扫码」只对人说一次。"""
        page = FakePage()
        profile = BookProfile(path=Path("书资料.yml"))
        seen: list[set[str] | None] = []

        def record(_page, _profile, announced=None):
            seen.append(announced)
            if len(seen) < len(WRITER_HOMES):
                raise RuntimeError("还没登上")

        with patch("publish.writer.wait_until_logged_in", side_effect=record):
            open_writer_home(page, profile)
        self.assertEqual(len(seen), len(WRITER_HOMES))
        self.assertIsNotNone(seen[0])
        self.assertTrue(all(item is seen[0] for item in seen))


class ScheduleStampTest(unittest.TestCase):
    def test_splits_date_and_time(self) -> None:
        self.assertEqual(split_schedule_stamp("2026-09-01 08:00"), ("2026-09-01", "08:00"))
        with self.assertRaises(PublishHalt):
            split_schedule_stamp("08:00")

    def test_missing_timed_switch_halts(self) -> None:
        page = FakePage()
        with self.assertRaises(PublishHalt):
            enable_timed_publish(page, "2026-09-01 08:00")


class PublishedChapterGuardTest(unittest.TestCase):
    def test_write_chapter_does_not_touch_published_body(self) -> None:
        page = FakePage()
        chapter = Chapter(
            sequence=1,
            title="工牌0727",
            body="澄江市。",
            path=Path("第001章-工牌0727.md"),
        )
        remote = RemoteChapter(title="第1章 工牌0727", chapter_id="1", published=True)
        report = PublishReport()
        profile = BookProfile(path=Path("书资料.yml"))
        write_chapter(page, chapter, remote, profile, report)
        self.assertEqual(report.updated_sequences, [])
        self.assertEqual(report.created_sequences, [])
        self.assertTrue(report.published_mismatches)
        self.assertEqual(page.gotos, [])

    def test_select_all_key_is_platform_shortcut(self) -> None:
        self.assertIn(select_all_key(), {"Meta+A", "Control+A"})

    def test_visibility_only_does_not_rewrite_body(self) -> None:
        page = FakePage()
        chapter = Chapter(
            sequence=1,
            title="工牌0727",
            body="澄江市。",
            path=Path("第001章-工牌0727.md"),
        )
        remote = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=False)
        report = PublishReport()
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        profile.cache_chapter(1, "c1", "old-fp", "草稿")
        with (
            patch("publish.writer.wait_for_chapter_editor"),
            patch("publish.writer.fill_chapter_number") as fill_number,
            patch("publish.writer.fill_chapter_title") as fill_title,
            patch("publish.writer.fill_chapter_body") as fill_body,
            patch("publish.writer.wait_for_cloud_save") as cloud_save,
            patch("publish.writer.submit_written_chapter") as submit,
            patch("publish.writer.save_profile"),
            patch("publish.writer.return_to_chapter_catalog"),
        ):
            write_chapter(
                page,
                chapter,
                remote,
                profile,
                report,
                scheduled_at="2026-09-01 08:00",
                visibility_only=True,
            )
        fill_number.assert_not_called()
        fill_title.assert_not_called()
        fill_body.assert_not_called()
        cloud_save.assert_not_called()
        submit.assert_called_once()
        self.assertEqual(profile.chapter_cache[1].fingerprint, "old-fp")
        self.assertEqual(profile.chapter_cache[1].visibility, profile.chapter_visibility)
        self.assertEqual(profile.chapter_cache[1].scheduled_at, "2026-09-01 08:00")
        self.assertEqual(report.updated_sequences, [1])

    def test_matching_fingerprint_still_writes_body(self) -> None:
        page = FakePage()
        chapter = Chapter(
            sequence=1,
            title="工牌0727",
            body="澄江市。",
            path=Path("第001章-工牌0727.md"),
        )
        remote = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=False)
        report = PublishReport()
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        profile.cache_chapter(1, "c1", chapter.fingerprint, "草稿")
        with (
            patch("publish.writer.wait_for_chapter_editor"),
            patch("publish.writer.fill_chapter_number") as fill_number,
            patch("publish.writer.fill_chapter_title") as fill_title,
            patch("publish.writer.fill_chapter_body") as fill_body,
            patch("publish.writer.wait_for_cloud_save"),
            patch("publish.writer.submit_written_chapter"),
            patch("publish.writer.save_profile"),
            patch("publish.writer.return_to_chapter_catalog"),
        ):
            write_chapter(page, chapter, remote, profile, report)
        fill_number.assert_called_once()
        fill_title.assert_called_once()
        fill_body.assert_called_once()


class CreatePlatformBookTest(unittest.TestCase):
    def test_auto_create_writes_book_id_from_url(self) -> None:
        from publish.manuscript import load_manuscript, save_profile
        from publish.writer import create_platform_book

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "封面.jpg").write_bytes(b"cover")
            profile = BookProfile(
                path=root / "书资料.yml",
                human_wait_seconds=1,
                fields={
                    "作品名称": "新书",
                    "频道": "女频",
                    "分类": "现代言情",
                    "简介": "简介",
                    "封面": "封面.jpg",
                },
            )
            save_profile(profile)
            manuscript = load_manuscript(root)
            page = FakePage(
                visible_texts=("创建新书", "立即创建", *LOGGED_IN_HINTS),
                reveal_after=2,
                reveal_url="https://fanqienovel.com/main/writer/book-info/888?type=2",
            )
            report = PublishReport()
            plan = PublishPlan(
                create=True,
                fields_to_write={"作品名称": "新书", "简介": "简介"},
                cover_to_upload="封面.jpg",
            )
            create_platform_book(page, manuscript, plan, report)
            self.assertTrue(report.created_book)
            self.assertEqual(manuscript.profile.book_id, "888")

    def test_missing_create_button_waits_for_url_then_binds(self) -> None:
        from publish.manuscript import load_manuscript, save_profile
        from publish.writer import create_platform_book

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = BookProfile(
                path=root / "书资料.yml",
                human_wait_seconds=2,
                fields={"作品名称": "新书"},
            )
            save_profile(profile)
            manuscript = load_manuscript(root)
            page = FakePage(
                reveal_after=1,
                reveal_url="https://fanqienovel.com/main/writer/book-info/777?type=2",
            )
            report = PublishReport()
            create_platform_book(page, manuscript, PublishPlan(create=True), report)
            self.assertEqual(manuscript.profile.book_id, "777")
            self.assertTrue(report.created_book)

    def test_manual_create_timeout_halts(self) -> None:
        from publish.manuscript import load_manuscript, save_profile
        from publish.writer import create_platform_book

        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            profile = BookProfile(
                path=root / "书资料.yml",
                human_wait_seconds=0,
                fields={"作品名称": "新书"},
            )
            save_profile(profile)
            manuscript = load_manuscript(root)
            page = FakePage()
            with self.assertRaises(PublishHalt) as halted:
                create_platform_book(page, manuscript, PublishPlan(create=True), PublishReport())
            self.assertIn("手工创建等待超时", str(halted.exception))


class ChapterCachePersistenceTest(unittest.TestCase):
    """写成功的章必须先落盘再回目录。回目录的导航一超时，
    这次写入连章 ID 一起丢，下次发稿又会把它当没建过。"""

    def _chapter(self) -> Chapter:
        return Chapter(
            sequence=3,
            title="春衣短三十套",
            body="保安军。",
            path=Path("第003章-春衣短三十套.md"),
        )

    def _enter_new_chapter(self, page: FakePage):
        def enter(*_args, **_kwargs) -> None:
            page.url = "https://fanqienovel.com/main/writer/10001/publish/?enter_from=newchapter"
        return enter

    def _cloud_save_assigns(self, page: FakePage, chapter_id: str):
        def assign(*_args, **_kwargs) -> None:
            page.url = f"https://fanqienovel.com/main/writer/10001/publish/{chapter_id}?type=1"
        return assign

    def test_cache_is_saved_before_returning_to_catalog(self) -> None:
        page = FakePage()
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        order: list[str] = []
        with (
            patch("publish.writer.open_create_chapter", side_effect=self._enter_new_chapter(page)),
            patch("publish.writer.wait_for_chapter_editor"),
            patch("publish.writer.fill_chapter_number"),
            patch("publish.writer.fill_chapter_title"),
            patch("publish.writer.fill_chapter_body"),
            patch("publish.writer.wait_for_cloud_save", side_effect=self._cloud_save_assigns(page, "900300")),
            patch("publish.writer.submit_written_chapter"),
            patch(
                "publish.writer.save_profile",
                side_effect=lambda *_: order.append("save"),
            ),
            patch(
                "publish.writer.return_to_chapter_catalog",
                side_effect=lambda *_: order.append("catalog"),
            ),
        ):
            write_chapter(page, self._chapter(), None, profile, PublishReport())
        self.assertEqual(order, ["save", "catalog"])
        self.assertEqual(profile.chapter_cache[3].chapter_id, "900300")

    def test_cache_survives_failed_return_to_catalog(self) -> None:
        page = FakePage()
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        saved: list[str] = []
        with (
            patch("publish.writer.open_create_chapter", side_effect=self._enter_new_chapter(page)),
            patch("publish.writer.wait_for_chapter_editor"),
            patch("publish.writer.fill_chapter_number"),
            patch("publish.writer.fill_chapter_title"),
            patch("publish.writer.fill_chapter_body"),
            patch("publish.writer.wait_for_cloud_save", side_effect=self._cloud_save_assigns(page, "900300")),
            patch("publish.writer.submit_written_chapter"),
            patch(
                "publish.writer.save_profile",
                side_effect=lambda prof: saved.append(prof.chapter_cache[3].chapter_id),
            ),
            patch(
                "publish.writer.return_to_chapter_catalog",
                side_effect=PublishHalt("打不开页面"),
            ),
        ):
            with self.assertRaises(PublishHalt):
                write_chapter(page, self._chapter(), None, profile, PublishReport())
        self.assertEqual(saved, ["900300"])

    def test_scheduled_publish_still_records_chapter_id(self) -> None:
        """按真实时序：新建章节进来时地址还没有章 ID，云端存稿才分配；
        定时发布提交完页面又跳回目录。章 ID 必须在这中间取到。"""
        page = FakePage()
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        profile.chapter_visibility = "定时发布"

        def enter_new_chapter(*_args, **_kwargs) -> None:
            page.url = "https://fanqienovel.com/main/writer/10001/publish/?enter_from=newchapter"

        def cloud_save_assigns_id(*_args, **_kwargs) -> None:
            page.url = "https://fanqienovel.com/main/writer/10001/publish/900500?type=1"

        def leave_editor(*_args, **_kwargs) -> None:
            page.url = chapter_catalog_url("10001")

        with (
            patch("publish.writer.open_create_chapter", side_effect=enter_new_chapter),
            patch("publish.writer.wait_for_chapter_editor"),
            patch("publish.writer.fill_chapter_number"),
            patch("publish.writer.fill_chapter_title"),
            patch("publish.writer.fill_chapter_body"),
            patch("publish.writer.wait_for_cloud_save", side_effect=cloud_save_assigns_id),
            patch("publish.writer.submit_written_chapter", side_effect=leave_editor),
            patch("publish.writer.save_profile"),
            patch("publish.writer.return_to_chapter_catalog"),
        ):
            write_chapter(
                page,
                self._chapter(),
                None,
                profile,
                PublishReport(),
                scheduled_at="2026-09-09 08:00",
            )
        self.assertEqual(profile.chapter_cache[3].chapter_id, "900500")


class ChapterActionsUseSharedObservationTest(unittest.TestCase):
    """一次发稿只读一次后台目录：执行阶段再读一次，只要少了草稿，
    整批「改可见性」就会被判成「找不到要更新的草稿」。"""

    def _manuscript(self) -> Manuscript:
        chapter = Chapter(
            sequence=78,
            title="公示",
            body="澄江市。",
            path=Path("第078章-公示.md"),
        )
        return Manuscript(
            directory=Path("."),
            profile=BookProfile(path=Path("书资料.yml"), book_id="10001"),
            chapters=(chapter,),
        )

    def test_execute_chapter_actions_does_not_reread_catalog(self) -> None:
        manuscript = self._manuscript()
        remote = RemoteChapter(title="第78章 公示", chapter_id="c78", published=False)
        plan = PublishPlan(
            book_id="10001",
            chapter_actions=(
                ChapterAction(
                    sequence=78,
                    action=ACTION_UPDATE_VISIBILITY,
                    chapter_id="c78",
                    scheduled_at="2026-10-06 15:00",
                ),
            ),
        )
        with (
            patch("publish.writer.list_remote_chapters") as reread,
            patch("publish.writer.write_chapter") as writer,
        ):
            execute_chapter_actions(
                FakePage(),
                manuscript,
                plan,
                PublishReport(),
                (remote,),
            )
        reread.assert_not_called()
        writer.assert_called_once()
        self.assertIs(writer.call_args.args[2], remote)

    def test_planned_chapter_missing_from_observation_still_halts(self) -> None:
        manuscript = self._manuscript()
        plan = PublishPlan(
            book_id="10001",
            chapter_actions=(
                ChapterAction(
                    sequence=78,
                    action=ACTION_UPDATE_VISIBILITY,
                    chapter_id="c78",
                ),
            ),
        )
        with patch("publish.writer.write_chapter"):
            with self.assertRaises(PublishHalt) as raised:
                execute_chapter_actions(
                    FakePage(),
                    manuscript,
                    plan,
                    PublishReport(),
                    (),
                )
        self.assertIn("第78章", str(raised.exception))


class PublishFailureContainmentTest(unittest.TestCase):
    """未预料的异常收进报告，浏览器照常关闭、书资料照常保存，任务不停在运行中。"""

    def test_unexpected_error_becomes_report_halt(self) -> None:
        manuscript = Manuscript(
            directory=Path("."),
            profile=BookProfile(path=Path("书资料.yml"), book_id="10001"),
            chapters=(),
        )
        page = FakePage()
        closed: list[str] = []

        class FakeContext:
            pages = [page]

            def close(self) -> None:
                closed.append("closed")

        class FakeChromium:
            @staticmethod
            def launch_persistent_context(**_kwargs) -> "FakeContext":
                return FakeContext()

        class FakePlaywright:
            chromium = FakeChromium()

            def __enter__(self) -> "FakePlaywright":
                return self

            def __exit__(self, *_args) -> bool:
                return False

        with (
            patch("playwright.sync_api.sync_playwright", return_value=FakePlaywright()),
            patch("publish.writer.open_writer_home", side_effect=RuntimeError("后台崩了")),
            patch("publish.writer.save_profile") as saver,
        ):
            report = run_publish(manuscript)
        self.assertIn("后台崩了", report.halted)
        self.assertEqual(closed, ["closed"])
        saver.assert_called_once()


class DraftBoxRequirementTest(unittest.TestCase):
    """草稿箱是水位的必要来源。只有确实没有东西可丢的空书才允许跳过。"""

    def _page_without_draft_box(self, rows: list[dict] | None = None) -> FakePage:
        return FakePage(evaluate_result=rows or [])

    def _only_catalog_tab(self, page_arg, names, seconds=None):
        del page_arg, seconds
        return names != ("草稿箱",)

    def test_halts_when_book_has_created_chapters(self) -> None:
        with patch("publish.writer.click_catalog_tab", side_effect=self._only_catalog_tab):
            with self.assertRaises(PublishHalt) as raised:
                list_remote_chapters(
                    self._page_without_draft_box(),
                    "10001",
                    drafts_required=True,
                )
        self.assertIn("草稿箱", str(raised.exception))

    def test_halts_when_catalog_tab_already_has_rows(self) -> None:
        rows = [{"text": "第1章 甲 已发布", "href": ""}]
        with patch("publish.writer.click_catalog_tab", side_effect=self._only_catalog_tab):
            with self.assertRaises(PublishHalt):
                list_remote_chapters(
                    self._page_without_draft_box(rows),
                    "10001",
                    drafts_required=False,
                )

    def test_empty_new_book_may_skip_missing_draft_box(self) -> None:
        with patch("publish.writer.click_catalog_tab", side_effect=self._only_catalog_tab):
            remotes = list_remote_chapters(
                self._page_without_draft_box(),
                "10001",
                drafts_required=False,
            )
        self.assertEqual(remotes, [])


class CatalogSwitchWaitTest(unittest.TestCase):
    """换标签时上一个标签的行还挂在 DOM 上。只等「有行」等于没等。"""

    class _SwitchingPage(FakePage):
        def __init__(self, payloads: list[list[dict]]) -> None:
            super().__init__()
            self.payloads = payloads
            self.reads = 0

        def evaluate(self, script: str, *args: object) -> object:
            del script, args
            index = min(self.reads, len(self.payloads) - 1)
            self.reads += 1
            return self.payloads[index]

    def test_waits_until_rows_change(self) -> None:
        stale = [{"text": "第1章 甲", "href": ""}]
        fresh = [{"text": "第78章 公示", "href": ""}]
        page = self._SwitchingPage([stale, stale, fresh])
        wait_for_catalog_switch(page, ("第1章 甲",))
        self.assertEqual(page.reads, 3)

    def test_empty_intermediate_state_is_not_taken_as_loaded(self) -> None:
        """旧行清空、新行还没渲染的那一瞬间也是「变了」，但这时候数到的是 0。"""
        stale = [{"text": "第1章 甲", "href": ""}]
        fresh = [{"text": "第78章 公示", "href": ""}]
        page = self._SwitchingPage([stale, [], [], fresh])
        wait_for_catalog_switch(page, ("第1章 甲",))
        self.assertEqual(page.reads, 4)

    def test_truly_empty_tab_uses_up_the_polls(self) -> None:
        page = self._SwitchingPage([[]])
        wait_for_catalog_switch(page, ("第1章 甲",))
        self.assertEqual(page.reads, CATALOG_SWITCH_POLLS)

    def test_returns_at_once_when_rows_already_changed(self) -> None:
        page = self._SwitchingPage([[{"text": "第78章 公示", "href": ""}]])
        wait_for_catalog_switch(page, ("第1章 甲",))
        self.assertEqual(page.reads, 1)

    def test_gives_up_after_bounded_polls(self) -> None:
        stale = [{"text": "第1章 甲", "href": ""}]
        page = self._SwitchingPage([stale])
        wait_for_catalog_switch(page, ("第1章 甲",))
        self.assertEqual(page.reads, CATALOG_SWITCH_POLLS)


class SpyRowLocator(FakeLocator):
    def __init__(self, *, visible: bool) -> None:
        super().__init__(visible=visible)
        self.waits = 0

    def wait_for(self, state: str = "visible", timeout: int = 15000) -> None:
        del state, timeout
        self.waits += 1


class RowsPage:
    def __init__(self, locator: SpyRowLocator) -> None:
        self._locator = locator

    def get_by_text(self, text: object, exact: bool = False) -> SpyRowLocator:
        del text, exact
        return self._locator


class CatalogRowRenderWaitTest(unittest.TestCase):
    """目录行没渲染就开始数会得到 0。水位读成 0，后台已有的章会被整本重建。"""

    def test_waits_for_first_row_when_nothing_rendered(self) -> None:
        locator = SpyRowLocator(visible=False)
        wait_for_catalog_rows(RowsPage(locator))
        self.assertEqual(locator.waits, 1)

    def test_does_not_wait_when_rows_already_rendered(self) -> None:
        locator = SpyRowLocator(visible=True)
        wait_for_catalog_rows(RowsPage(locator))
        self.assertEqual(locator.waits, 0)


class StrandedEditorGuardTest(unittest.TestCase):
    """真实故障：第 8 章的正文被写进了第 7 章。

    导航到新章页两次都超时后，兜底去点「创建章节」；点击没生效时页面
    还停在上一章的编辑器上，那里同样有标题框、地址同样含 /publish/，
    于是这一章的正文覆盖了上一章，章缓存出现两章共用一个章 ID。
    """

    def _chapter(self) -> Chapter:
        return Chapter(
            sequence=8,
            title="斥候带回的牙旗",
            body="保安军。",
            path=Path("第008章-斥候带回的牙旗.md"),
        )

    def test_create_halts_when_editor_still_holds_another_chapter(self) -> None:
        page = FakePage()
        page.url = "https://fanqienovel.com/main/writer/10001/publish/7681862352014164542?type=1"
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        report = PublishReport()
        with (
            patch("publish.writer.open_create_chapter"),
            patch("publish.writer.wait_for_chapter_editor"),
            patch("publish.writer.fill_chapter_body") as body,
            patch("publish.writer.submit_written_chapter") as submit,
            patch("publish.writer.save_profile") as saver,
            patch("publish.writer.return_to_chapter_catalog"),
        ):
            with self.assertRaises(PublishHalt) as raised:
                write_chapter(page, self._chapter(), None, profile, report)
        self.assertIn("7681862352014164542", str(raised.exception))
        self.assertIn("没有进入新章页", str(raised.exception))
        body.assert_not_called()
        submit.assert_not_called()
        saver.assert_not_called()
        self.assertEqual(profile.chapter_cache, {})

    def test_update_path_is_not_blocked_by_the_guard(self) -> None:
        page = FakePage()
        page.url = "https://fanqienovel.com/main/writer/10001/publish/c9?type=1"
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        remote = RemoteChapter(title="第8章 斥候带回的牙旗", chapter_id="c9", published=False)
        with (
            patch("publish.writer.open_remote_chapter"),
            patch("publish.writer.wait_for_chapter_editor"),
            patch("publish.writer.fill_chapter_number"),
            patch("publish.writer.fill_chapter_title"),
            patch("publish.writer.fill_chapter_body"),
            patch("publish.writer.wait_for_cloud_save"),
            patch("publish.writer.submit_written_chapter"),
            patch("publish.writer.save_profile"),
            patch("publish.writer.return_to_chapter_catalog"),
        ):
            write_chapter(page, self._chapter(), remote, profile, PublishReport())
        self.assertEqual(profile.chapter_cache[8].chapter_id, "c9")


class CreateChapterButtonTest(unittest.TestCase):
    """点「创建章节」之后必须确认真的换了页：新章页的地址不带章 ID。"""

    def test_stuck_when_click_left_us_on_the_catalog(self) -> None:
        """目录页地址同样不带章 ID。只判「没有章 ID」会把没生效的点击当成进了新章页。"""
        button = FakeLocator(visible=True)
        page = FakePage(roles={("button", "创建章节"): button})
        page.url = chapter_catalog_url("10001")
        self.assertEqual(click_create_chapter_button(page), CREATE_CHAPTER_STUCK)
        self.assertEqual(button.clicks, 1)

    def test_stuck_when_still_on_another_chapter(self) -> None:
        button = FakeLocator(visible=True)
        page = FakePage(roles={("button", "创建章节"): button})
        page.url = "https://fanqienovel.com/main/writer/10001/publish/777?type=1"
        self.assertEqual(click_create_chapter_button(page), CREATE_CHAPTER_STUCK)
        self.assertEqual(button.clicks, 1)
        self.assertEqual(page.timeouts, CREATE_CHAPTER_POLLS)

    def test_ok_on_the_bare_publish_page(self) -> None:
        button = FakeLocator(visible=True)
        page = FakePage(roles={("button", "创建章节"): button})
        page.url = "https://fanqienovel.com/main/writer/10001/publish/?enter_from=newchapter"
        self.assertEqual(click_create_chapter_button(page), CREATE_CHAPTER_OK)

    def test_missing_when_button_missing(self) -> None:
        self.assertEqual(click_create_chapter_button(FakePage()), CREATE_CHAPTER_MISSING)


class CatalogWaitWiringTest(unittest.TestCase):
    """两处等待必须真的被接进调用链，光有函数不算数。"""

    def test_collect_catalog_rows_waits_for_rows_first(self) -> None:
        page = FakePage(evaluate_result=[])
        with patch("publish.writer.wait_for_catalog_rows") as waiter:
            collect_catalog_rows(page)
        waiter.assert_called_once_with(page)

    def test_list_remote_chapters_waits_for_tab_switch(self) -> None:
        page = FakePage(evaluate_result=[{"text": "第1章 甲", "href": ""}])
        with (
            patch("publish.writer.click_catalog_tab", return_value=True),
            patch("publish.writer.wait_for_catalog_switch") as waiter,
        ):
            list_remote_chapters(page, "10001")
        waiter.assert_called_once()
        self.assertEqual(waiter.call_args.args[1], ("第1章 甲",))


class ScrollAndCollectTest(unittest.TestCase):
    """长目录是虚拟滚动的：滚出视口的行会被移出 DOM。

    滚到底再取一次快照，拿到的只是最后那一屏——93 章的书会读成几章。
    """

    class _VirtualPage(FakePage):
        def __init__(self, windows: list[list[dict]]) -> None:
            super().__init__()
            self.windows = windows
            self.step = 0

        def evaluate(self, script: str, *args: object) -> object:
            del script, args
            return self.windows[min(self.step, len(self.windows) - 1)]

        def wait_for_timeout(self, milliseconds: int) -> None:
            super().wait_for_timeout(milliseconds)
            self.step += 1

    def test_rows_scrolled_out_of_view_are_kept(self) -> None:
        page = self._VirtualPage([
            [{"text": "第1章 甲", "href": ""}, {"text": "第2章 乙", "href": ""}],
            [{"text": "第3章 丙", "href": ""}, {"text": "第4章 丁", "href": ""}],
            [{"text": "第5章 戊", "href": ""}],
        ])
        rows = scroll_and_collect(page)
        self.assertEqual(
            [row["text"] for row in rows],
            ["第1章 甲", "第2章 乙", "第3章 丙", "第4章 丁", "第5章 戊"],
        )

    def test_stops_once_no_new_rows_appear(self) -> None:
        page = self._VirtualPage([[{"text": "第1章 甲", "href": ""}]])
        rows = scroll_and_collect(page)
        self.assertEqual(len(rows), 1)
        self.assertLess(page.step, CATALOG_SCROLL_STEPS)

    def test_duplicate_rows_across_steps_are_merged(self) -> None:
        same = [{"text": "第1章 甲", "href": ""}]
        page = self._VirtualPage([same, same, same])
        self.assertEqual(len(scroll_and_collect(page)), 1)


class LeafRowsOnlyTest(unittest.TestCase):
    """目录抓取只能收叶子行。

    跨多章的祖先容器也含「第N章」，把它当成一行会串状态：标题取容器里第一个章号，
    而「已发布」是在整块文本里搜的——定时发布的章会被误标成已发布，整轮跳过。
    边滚边收还会让容器每步都算新行，滚动永远停不下来。
    """

    def test_js_keeps_only_elements_with_one_chapter_marker(self) -> None:
        import re as _re

        marker = _re.search(
            r"\.filter\(\s*\(el\) =>(.+?)\);", COLLECT_CHAPTER_ROWS_JS, _re.S
        )
        self.assertIsNotNone(marker, "找不到行过滤条件")
        condition = marker.group(1)
        self.assertIn("match", condition)
        self.assertIn("length === 1", condition)

    def test_container_row_would_mislabel_a_scheduled_chapter(self) -> None:
        """留一份反例：容器文本混着别章的「已发布」，解析出来就是错的状态。"""
        container = "第85章 药 定时发布 2026-10-14 08:00 第80章 公示 已发布"
        published, visibility = catalog_row_status(container)
        self.assertTrue(published)
        self.assertEqual(compact_chapter_title(container), "第85章 药")


class ScrollIdleStopTest(unittest.TestCase):
    """一步没新增可能只是渲染卡了一下，连着几步没新增才算到底。"""

    class _StallingPage(FakePage):
        def __init__(self, windows: list[list[dict]]) -> None:
            super().__init__()
            self.windows = windows
            self.step = 0

        def evaluate(self, script: str, *args: object) -> object:
            del script, args
            return self.windows[min(self.step, len(self.windows) - 1)]

        def wait_for_timeout(self, milliseconds: int) -> None:
            super().wait_for_timeout(milliseconds)
            self.step += 1

    def test_one_idle_step_does_not_stop_the_scroll(self) -> None:
        first = [{"text": "第1章 甲", "href": ""}]
        late = [{"text": "第2章 乙", "href": ""}]
        page = self._StallingPage([first, first, late])
        rows = scroll_and_collect(page)
        self.assertEqual([row["text"] for row in rows], ["第1章 甲", "第2章 乙"])

    def test_stops_after_consecutive_idle_steps(self) -> None:
        page = self._StallingPage([[{"text": "第1章 甲", "href": ""}]])
        scroll_and_collect(page)
        self.assertLessEqual(page.step, CATALOG_SCROLL_IDLE_STEPS + 1)


class EmptyBookExceptionTest(unittest.TestCase):
    """章缓存有记录但章 ID 全空的老书不是空新书——那正是修复前会产生的状态。"""

    def _only_catalog_tab(self, page_arg, names, seconds=None):
        del page_arg, seconds
        return names != ("草稿箱",)

    def test_cache_with_blank_ids_still_requires_the_draft_box(self) -> None:
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        for sequence in range(1, 89):
            profile.cache_chapter(sequence, "", f"fp{sequence}", "定时发布")
        self.assertTrue(profile.has_created_chapters())
        with patch("publish.writer.click_catalog_tab", side_effect=self._only_catalog_tab):
            with self.assertRaises(PublishHalt) as raised:
                list_remote_chapters(
                    FakePage(evaluate_result=[]),
                    "10001",
                    drafts_required=profile.has_created_chapters(),
                )
        self.assertIn("草稿箱", str(raised.exception))

    def test_truly_empty_cache_is_still_an_empty_new_book(self) -> None:
        profile = BookProfile(path=Path("书资料.yml"), book_id="10001")
        self.assertFalse(profile.has_created_chapters())
        with patch("publish.writer.click_catalog_tab", side_effect=self._only_catalog_tab):
            remotes = list_remote_chapters(
                FakePage(evaluate_result=[]),
                "10001",
                drafts_required=profile.has_created_chapters(),
            )
        self.assertEqual(remotes, [])


class CatalogRowStatusTest(unittest.TestCase):
    """一行一章时状态各归各的，不会互相串。

    「祖先容器不算一行」那条契约在这里测不到：FakePage.evaluate 不执行 JS，
    喂进去的已经是分好的叶子行。那条只能由 LeafRowsOnlyTest 守着。
    """

    def test_each_row_keeps_its_own_status(self) -> None:
        rows = [
            {"text": "第85章 药 定时发布 2026-10-14 08:00", "href": ""},
            {"text": "第80章 公示 已发布", "href": ""},
        ]
        page = FakePage(evaluate_result=rows)
        remotes = collect_catalog_rows(page)
        by_title = {item.title: item for item in remotes}
        self.assertFalse(by_title["第85章 药"].published)
        self.assertTrue(by_title["第80章 公示"].published)


class CreateChapterFailureMessageTest(unittest.TestCase):
    def test_clicked_but_not_landed_says_so_for_every_button_name(self) -> None:
        """按钮名有三个候选，点中哪个都得报「没进入新章页」。

        事后回头数按钮只认得出第一个名字，另外两个会被误报成「找不到」——
        运维照文案去找按钮，按钮就在眼前，真正的故障反倒被盖住了。
        """
        for name in CREATE_CHAPTER_BUTTONS:
            with self.subTest(name=name):
                button = FakeLocator(visible=True)
                page = FakePage(roles={("button", name): button})
                page.url = chapter_catalog_url("10001")
                with self.assertRaises(PublishHalt) as raised:
                    open_create_chapter(page, "")
                self.assertEqual(button.clicks, 1)
                self.assertIn("没进入新章页", str(raised.exception))

    def test_missing_button_still_says_missing(self) -> None:
        page = FakePage()
        with self.assertRaises(PublishHalt) as raised:
            open_create_chapter(page, "")
        self.assertIn("找不到", str(raised.exception))


class ReportMatchesHaltTest(unittest.TestCase):
    """守卫停机时报告不能还说「新建了第N章」。"""

    def test_stranded_create_is_not_reported_as_created(self) -> None:
        page = FakePage()
        page.url = "https://fanqienovel.com/main/writer/10001/publish/777?type=1"
        report = PublishReport()
        chapter = Chapter(sequence=8, title="甲", body="乙", path=Path("第008章-甲.md"))
        with (
            patch("publish.writer.open_create_chapter"),
            patch("publish.writer.wait_for_chapter_editor"),
        ):
            with self.assertRaises(PublishHalt):
                write_chapter(page, chapter, None, BookProfile(path=Path("书资料.yml")), report)
        self.assertEqual(report.created_sequences, [])


if __name__ == "__main__":
    unittest.main()
