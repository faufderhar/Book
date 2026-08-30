from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from publish.manuscript import BookProfile, load_manuscript, save_profile
from publish.plan import (
    HALT_BOUND_BOOK_UNOPENABLE,
    HALT_MANY_SEARCH_HITS,
    HALT_NO_SEARCH_HIT,
    MODE_DISCOVER,
    MODE_DRY_RUN,
    MODE_PUBLISH,
    CommandMode,
    RemoteObservation,
    SearchHit,
    plan_publish,
)


def write_manuscript(
    root: Path,
    *,
    title: str = "工牌不认婚约",
    book_id: str = "",
    fields: dict[str, object] | None = None,
) -> BookProfile:
    volume = root / "卷一"
    volume.mkdir()
    (volume / "第001章-工牌0727.md").write_text(
        "# 第1章 工牌0727\n\n澄江市。\n",
        encoding="utf-8",
    )
    profile_fields: dict[str, object] = {
        "作品名称": title,
        "频道": "女频",
        "分类": "现代言情",
        "简介": "长简介一段。",
        "封面": "",
        "标签": [],
    }
    if fields:
        profile_fields.update(fields)
    profile = BookProfile(
        path=root / "书资料.yml",
        book_id=book_id,
        fields=profile_fields,
    )
    save_profile(profile)
    return profile


class ClaimPlanTest(unittest.TestCase):
    def test_one_hit_claims_that_book_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            hit = SearchHit(book_id="10001", row_text="工牌不认婚约")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(hit,)),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "10001")
            self.assertEqual(plan.candidates, ())

    def test_zero_hits_halts_without_create(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            unrelated = SearchHit(book_id="9", row_text="认罪会传染 连载")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(unrelated,)),
            )
            self.assertEqual(plan.halt_reason, HALT_NO_SEARCH_HIT)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "")
            self.assertEqual(plan.candidates, ())

    def test_many_hits_halt_and_list_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            first = SearchHit(book_id="1", row_text="工牌不认婚约 连载")
            second = SearchHit(book_id="2", row_text="工牌不认婚约番外 已签约")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(first, second)),
            )
            self.assertEqual(plan.halt_reason, HALT_MANY_SEARCH_HITS)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "")
            self.assertEqual(plan.candidates, (first, second))

    def test_title_plus_serial_or_signed_suffix_is_one_book(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            serial_row = SearchHit(book_id="10001", row_text="工牌不认婚约 连载")
            signed_row = SearchHit(book_id="10001", row_text="工牌不认婚约已签约")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(serial_row, signed_row)),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "10001")
            self.assertEqual(plan.candidates, ())

    def test_similar_title_alone_is_not_an_exact_hit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            sequel = SearchHit(book_id="20002", row_text="工牌不认婚约续 连载")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(sequel,)),
            )
            self.assertEqual(plan.halt_reason, HALT_NO_SEARCH_HIT)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "")

    def test_two_work_names_count_as_many(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            first = SearchHit(
                book_id="1",
                row_text="工牌不认婚约 连载",
                work_name="工牌不认婚约",
            )
            second = SearchHit(
                book_id="2",
                row_text="工牌不认婚约续 连载",
                work_name="工牌不认婚约续",
            )
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_DRY_RUN),
                RemoteObservation(search_hits=(first, second)),
            )
            self.assertEqual(plan.halt_reason, HALT_MANY_SEARCH_HITS)
            self.assertFalse(plan.create)
            self.assertEqual(plan.candidates, (first, second))

    def test_bound_id_unopenable_halts_without_create(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            would_bind_if_searched = SearchHit(book_id="20002", row_text="工牌不认婚约")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(
                    search_hits=(would_bind_if_searched,),
                    bound_book_openable=False,
                ),
            )
            self.assertEqual(plan.halt_reason, HALT_BOUND_BOOK_UNOPENABLE)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "")
            self.assertEqual(plan.candidates, ())

    def test_bound_id_openable_keeps_id_and_ignores_search(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            other = SearchHit(book_id="20002", row_text="工牌不认婚约番外 连载")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(other,), bound_book_openable=True),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "10001")

    def test_discover_never_creates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            empty = plan_publish(
                manuscript,
                CommandMode(MODE_DISCOVER, allow_create=True),
                RemoteObservation(),
            )
            self.assertEqual(empty.halt_reason, HALT_NO_SEARCH_HIT)
            self.assertFalse(empty.create)
            claimed = plan_publish(
                manuscript,
                CommandMode(MODE_DISCOVER, allow_create=True),
                RemoteObservation(
                    search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约 连载"),)
                ),
            )
            self.assertIsNone(claimed.halt_reason)
            self.assertFalse(claimed.create)
            self.assertEqual(claimed.book_id, "10001")

    def test_zero_hits_without_allow_create_do_not_create_even_if_required_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                fields={"频道": "", "分类": "", "简介": "", "封面": "", "标签": []},
            )
            manuscript = load_manuscript(root)
            self.assertTrue(manuscript.profile.missing_create_fields(root))
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=False),
                RemoteObservation(),
            )
            self.assertEqual(plan.halt_reason, HALT_NO_SEARCH_HIT)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "")
            self.assertEqual(plan.missing_fields, ())

    def test_one_hit_still_claims_when_allow_create(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(
                    search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约 已签约"),)
                ),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "10001")

    def test_plan_does_not_mutate_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            original_book_id = manuscript.profile.book_id
            original_fields = dict(manuscript.profile.fields)
            plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约"),)),
            )
            self.assertEqual(manuscript.profile.book_id, original_book_id)
            self.assertEqual(manuscript.profile.fields, original_fields)

    def test_orchestrator_leaves_settings_and_chapters_empty(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(
                    search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约 连载"),)
                ),
            )
            self.assertEqual(plan.book_id, "10001")
            self.assertEqual(plan.fields_to_write, {})
            self.assertIsNone(plan.cover_to_upload)
            self.assertEqual(plan.empty_keys_to_add, ())
            self.assertEqual(plan.chapter_actions, ())
            self.assertEqual(plan.extra_remote_chapters, ())
            self.assertEqual(plan.locked_fields, ())
            self.assertEqual(plan.missing_fields, ())


if __name__ == "__main__":
    unittest.main()
