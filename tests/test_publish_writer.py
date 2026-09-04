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
    PublishHalt,
    PublishReport,
    apply_planned_settings,
    click_catalog_tab,
    click_first_visible_name,
    dismiss_popups,
    dismiss_tour_guide,
    chapter_catalog_url,
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
    wait_for_chapter_editor,
    wait_until_logged_in,
    wait_until_publish_settings,
    write_chapter,
)
from publish.plan import PublishPlan, RemoteChapter, SearchHit


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

    def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.gotos.append(url)
        self.url = url
        if self.fail_first_goto and len(self.gotos) == 1:
            raise RuntimeError("first home down")

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
        page = FakePage(roles={("tab", "章节管理"): tab})
        list_remote_chapters(page, "7679308798468557886")
        self.assertEqual(page.gotos, [chapter_catalog_url("7679308798468557886")])
        self.assertGreaterEqual(tab.clicks, 1)

    def test_list_remote_chapters_halts_when_catalog_tab_missing(self) -> None:
        page = FakePage()
        with self.assertRaises(PublishHalt) as raised:
            list_remote_chapters(page, "7679308798468557886")
        self.assertIn("未确认水位", str(raised.exception))
        self.assertEqual(page.gotos, [chapter_catalog_url("7679308798468557886")])

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


if __name__ == "__main__":
    unittest.main()
