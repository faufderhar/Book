from __future__ import annotations

import io
import unittest

from book.jobs import (
    PHASE_DONE,
    PHASE_FAILED,
    PHASE_PENDING,
    PHASE_RUNNING,
    Phase,
    TaskProgress,
    buffer_lines,
    format_elapsed,
)


class TaskProgressTest(unittest.TestCase):
    def setUp(self) -> None:
        self.progress = TaskProgress(
            phases=[
                Phase(key="login", label="打开作家后台"),
                Phase(key="catalog", label="读后台目录"),
                Phase(key="lists", label="采各榜"),
            ]
        )

    def test_begin_advance_finish_and_fail(self) -> None:
        self.progress.begin("login")
        login = self.progress.snapshot()[0]
        self.assertEqual(login["state"], PHASE_RUNNING)
        self.assertEqual(login["total"], 0)

        self.progress.begin("catalog")
        self.progress.add_total("catalog", 3)
        self.progress.add_total("catalog", 2)
        self.progress.advance("catalog", step=2, note="章节管理第2页")
        catalog = self._phase("catalog")
        self.assertEqual(catalog["total"], 5)
        self.assertEqual(catalog["done"], 2)
        self.assertEqual(catalog["note"], "章节管理第2页")

        self.progress.finish("catalog")
        catalog = self._phase("catalog")
        self.assertEqual(catalog["state"], PHASE_DONE)
        self.assertEqual(catalog["done"], 5)

        self.progress.fail("lists", note="HTTP 403")
        lists = self._phase("lists")
        self.assertEqual(lists["state"], PHASE_FAILED)
        self.assertEqual(lists["note"], "HTTP 403")

    def test_unknown_keys_are_ignored(self) -> None:
        self.progress.begin("missing")
        self.progress.add_total("missing", 3)
        self.progress.advance("missing")
        self.progress.finish("missing")
        self.progress.fail("missing", note="x")
        self.assertEqual(
            [item["key"] for item in self.progress.snapshot()],
            ["login", "catalog", "lists"],
        )
        self.assertEqual(self._phase("login")["state"], PHASE_PENDING)

    def test_zero_total_does_not_invent_a_percent(self) -> None:
        self.progress.begin("login")
        login = self._phase("login")
        self.assertEqual(login["total"], 0)
        self.assertNotIn("percent", login)

    def test_snapshot_is_isolated_from_later_writes(self) -> None:
        self.progress.begin("lists", total=3)
        frozen = self.progress.snapshot()
        self.progress.advance("lists")
        self.assertEqual(frozen[2]["done"], 0)
        self.assertEqual(self._phase("lists")["done"], 1)

    def test_advance_with_zero_step_only_changes_note(self) -> None:
        self.progress.begin("catalog", total=2)
        self.progress.advance("catalog", step=0, note="正在写")
        catalog = self._phase("catalog")
        self.assertEqual(catalog["done"], 0)
        self.assertEqual(catalog["note"], "正在写")

    def test_buffer_lines_drops_blanks(self) -> None:
        self.assertEqual(buffer_lines(io.StringIO("a\n\nb\n")), ["a", "b"])
        self.assertEqual(buffer_lines(None), [])

    def test_format_elapsed(self) -> None:
        self.assertEqual(format_elapsed(0), "00:00")
        self.assertEqual(format_elapsed(252), "04:12")

    def _phase(self, key: str) -> dict:
        for item in self.progress.snapshot():
            if item["key"] == key:
                return item
        raise AssertionError(key)


if __name__ == "__main__":
    unittest.main()
