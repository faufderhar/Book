from __future__ import annotations

import unittest

from publish.writer import (
    CATALOG_PAGE_NUMBERS_JS,
    CLICK_CATALOG_PAGE_JS,
    VISIBILITY_DRAFT,
    collect_paged_catalog_rows,
)

from test_publish_writer import FakeLocator, FakePage


class LeakedInactivePagerPage(FakePage):
    """草稿箱没有分页，但章节管理的 7 页页码还挂在非活动面板上。"""

    def __init__(self, rows: list[str], leaked_pages: int, leaked_active: int) -> None:
        super().__init__()
        self.rows = rows
        self.leaked_pages = leaked_pages
        self.leaked_active = leaked_active
        self.clicked_pages: list[int] = []

    def evaluate(self, script: str, *args: object) -> object:
        scoped = ".arco-tabs-pane" in script or "tabpanel" in script
        if "click-catalog-page" in script:
            number = int(args[0]) if args else 0
            self.clicked_pages.append(number)
            return False
        if "li[aria-label]" in script:
            if scoped:
                return []
            return [
                {"number": number, "active": number == self.leaked_active}
                for number in range(1, self.leaked_pages + 1)
            ]
        return [{"text": text, "href": ""} for text in self.rows]

    def get_by_text(self, text: str, exact: bool = False) -> FakeLocator:
        del exact
        if hasattr(text, "search"):
            return FakeLocator(visible=any(bool(text.search(row)) for row in self.rows))
        return super().get_by_text(text)


class DraftBoxMustNotWalkInactiveTabPagerTest(unittest.TestCase):
    def test_js_reads_page_numbers_inside_the_active_tab_pane(self) -> None:
        self.assertIn(".arco-tabs-pane", CATALOG_PAGE_NUMBERS_JS)
        self.assertIn("tabpanel", CATALOG_PAGE_NUMBERS_JS)
        self.assertIn("box.left < 0", CATALOG_PAGE_NUMBERS_JS)

    def test_click_targets_the_active_tab_pane(self) -> None:
        self.assertIn("click-catalog-page", CLICK_CATALOG_PAGE_JS)
        self.assertIn(".arco-tabs-pane", CLICK_CATALOG_PAGE_JS)
        self.assertIn("tabpanel", CLICK_CATALOG_PAGE_JS)

    def test_draft_box_without_pager_does_not_follow_leaked_pages(self) -> None:
        page = LeakedInactivePagerPage(
            rows=["第93章 甲 草稿", "第94章 乙 草稿", "第95章 丙 草稿"],
            leaked_pages=7,
            leaked_active=7,
        )
        remotes = collect_paged_catalog_rows(
            page,
            "草稿箱",
            published=False,
            visibility=VISIBILITY_DRAFT,
        )
        self.assertEqual(len(remotes), 3)
        self.assertEqual(page.clicked_pages, [])
