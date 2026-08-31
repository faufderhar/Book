from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from publish.manuscript import (
    SERIAL_FINISHED,
    VISIBILITY_DRAFT,
    VISIBILITY_SCHEDULE,
    BookProfile,
    load_manuscript,
    save_profile,
)
from publish.plan import (
    ACTION_CREATE_DRAFT,
    ACTION_PUBLISHED_MISMATCH,
    ACTION_SKIP,
    ACTION_UPDATE_DRAFT,
    HALT_BOUND_BOOK_UNOPENABLE,
    HALT_EMPTY_REMOTE_CATALOG,
    HALT_MANY_SEARCH_HITS,
    HALT_MISSING_CREATE_FIELDS,
    HALT_NO_SEARCH_HIT,
    MODE_DISCOVER,
    MODE_DRY_RUN,
    MODE_PUBLISH,
    CommandMode,
    RemoteChapter,
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
    chapter_specs: tuple[tuple[int, str, str], ...] | None = None,
    cover_name: str = "",
    serial_status: str = "连载",
    max_chapters_per_run: int = 20,
) -> BookProfile:
    volume = root / "卷一"
    volume.mkdir()
    specs = chapter_specs or ((1, "工牌0727", "澄江市。"),)
    for sequence, chapter_title, body in specs:
        (volume / f"第{sequence:03d}章-{chapter_title}.md").write_text(
            f"# 第{sequence}章 {chapter_title}\n\n{body}\n",
            encoding="utf-8",
        )
    if cover_name:
        (root / cover_name).write_bytes(b"cover")
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
        serial_status=serial_status,
        max_chapters_per_run=max_chapters_per_run,
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

    def test_same_title_different_ids_count_as_many(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            first = SearchHit(book_id="10001", row_text="工牌不认婚约 连载")
            second = SearchHit(book_id="10002", row_text="工牌不认婚约 已签约")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(first, second)),
            )
            self.assertEqual(plan.halt_reason, HALT_MANY_SEARCH_HITS)
            self.assertFalse(plan.create)
            self.assertEqual(plan.candidates, (first, second))

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

    def test_claim_without_catalog_does_not_plan_chapters(self) -> None:
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
            self.assertIsNone(plan.cover_to_upload)
            self.assertEqual(plan.empty_keys_to_add, ())
            self.assertEqual(plan.chapter_actions, ())
            self.assertEqual(plan.extra_remote_chapters, ())
            self.assertNotIn("标签", plan.fields_to_write)
            self.assertNotIn("封面", plan.fields_to_write)


class CreatePlanTest(unittest.TestCase):
    def test_allow_create_zero_hits_with_required_fields_creates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertTrue(plan.create)
            self.assertEqual(plan.book_id, "")
            self.assertEqual(plan.cover_to_upload, "封面.jpg")

    def test_claim_does_not_halt_on_empty_cover_or_tags(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            self.assertEqual(manuscript.profile.field_text("封面"), "")
            self.assertEqual(manuscript.profile.tag_list(), [])
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约"),)),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertFalse(plan.create)
            self.assertEqual(plan.book_id, "10001")

    def test_allow_create_missing_required_fields_halts_with_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, fields={"简介": "", "封面": ""})
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(),
            )
            self.assertEqual(plan.halt_reason, HALT_MISSING_CREATE_FIELDS)
            self.assertFalse(plan.create)
            self.assertIn("简介", plan.missing_fields)
            self.assertIn("封面", plan.missing_fields)

    def test_empty_cover_key_with_conventional_file_is_uploadable(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, cover_name="封面.png")
            manuscript = load_manuscript(root)
            self.assertEqual(manuscript.profile.field_text("封面"), "")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约"),)),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.cover_to_upload, "封面.png")
            self.assertEqual(manuscript.profile.field_text("封面"), "")

    def test_discover_with_allow_create_still_does_not_create(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_DISCOVER, allow_create=True),
                RemoteObservation(),
            )
            self.assertEqual(plan.halt_reason, HALT_NO_SEARCH_HIT)
            self.assertFalse(plan.create)


class SettingsPlanTest(unittest.TestCase):
    def test_empty_keys_are_not_written_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, fields={"主角姓名": "", "标签": []})
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约"),)),
            )
            self.assertNotIn("主角姓名", plan.fields_to_write)
            self.assertNotIn("标签", plan.fields_to_write)
            self.assertNotIn("封面", plan.fields_to_write)
            self.assertEqual(plan.fields_to_write.get("作品名称"), "工牌不认婚约")

    def test_locked_fields_are_reported_without_halting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            remote = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=False)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(
                    locked_fields=("作品名称",),
                    remote_chapters=(remote,),
                    catalog_observed=True,
                ),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.locked_fields, ("作品名称",))
            self.assertNotIn("作品名称", plan.fields_to_write)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)

    def test_new_form_labels_become_empty_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_DISCOVER),
                RemoteObservation(
                    search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约"),),
                    form_labels=("作品名称", "签约状态"),
                ),
            )
            self.assertFalse(plan.create)
            self.assertEqual(plan.empty_keys_to_add, ("签约状态",))

    def test_serial_status_only_written_when_finished(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root)
            manuscript = load_manuscript(root)
            ongoing = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约"),)),
            )
            self.assertNotIn("连载状态", ongoing.fields_to_write)
            manuscript.profile.serial_status = SERIAL_FINISHED
            finished = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(search_hits=(SearchHit(book_id="10001", row_text="工牌不认婚约"),)),
            )
            self.assertEqual(finished.fields_to_write.get("连载状态"), SERIAL_FINISHED)


class ChapterPlanTest(unittest.TestCase):
    def test_published_title_match_skips_and_mismatch_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (1, "工牌0727", "澄江市。"),
                    (2, "档案先于报表", "档案室。"),
                ),
            )
            manuscript = load_manuscript(root)
            matched = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True)
            mismatched = RemoteChapter(title="第2章 另一标题", chapter_id="c2", published=True)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(matched, mismatched), catalog_observed=True),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_SKIP)
            self.assertEqual(plan.chapter_actions[1].action, ACTION_PUBLISHED_MISMATCH)
            self.assertIn("另一标题", plan.chapter_actions[1].reason)

    def test_draft_fingerprint_change_updates_and_missing_creates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (1, "工牌0727", "澄江市。"),
                    (2, "档案先于报表", "档案室。"),
                ),
            )
            manuscript = load_manuscript(root)
            first = manuscript.chapters[0]
            manuscript.profile.set_binding(1, "c1", "old-fingerprint", VISIBILITY_DRAFT)
            remote_first = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=False)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(remote_first,), catalog_observed=True),
            )
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[1].action, ACTION_CREATE_DRAFT)
            self.assertNotEqual(first.fingerprint, "old-fingerprint")

    def test_extra_remote_chapters_are_reported(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            local_remote = RemoteChapter(title="第1章 工牌0727", chapter_id="c1")
            extra = RemoteChapter(title="第9章 多余", chapter_id="c9")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(local_remote, extra), catalog_observed=True),
            )
            self.assertEqual(plan.extra_remote_chapters, (extra,))

    def test_claimed_existing_empty_catalog_halts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(catalog_observed=True),
            )
            self.assertEqual(plan.halt_reason, HALT_EMPTY_REMOTE_CATALOG)
            self.assertEqual(plan.chapter_actions, ())
            self.assertEqual(plan.book_id, "10001")

    def test_created_book_empty_catalog_writes_from_first_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(catalog_observed=True),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertTrue(plan.create)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_CREATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].sequence, 1)

    def test_write_budget_ignores_skips_and_leaves_remainder(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                max_chapters_per_run=2,
                chapter_specs=(
                    (1, "工牌0727", "一。"),
                    (2, "档案先于报表", "二。"),
                    (3, "过桥到期", "三。"),
                    (4, "走廊上的签名", "四。"),
                ),
            )
            manuscript = load_manuscript(root)
            published = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True)
            extra = RemoteChapter(title="第8章 多余", chapter_id="c8")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(published, extra), catalog_observed=True),
            )
            actions = {action.sequence: action.action for action in plan.chapter_actions}
            self.assertEqual(actions[1], ACTION_SKIP)
            self.assertEqual(actions[2], ACTION_CREATE_DRAFT)
            self.assertEqual(actions[3], ACTION_CREATE_DRAFT)
            self.assertNotIn(4, actions)
            self.assertEqual(plan.extra_remote_chapters, (extra,))

    def test_dry_run_claim_failure_has_no_chapter_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_DRY_RUN, allow_create=True),
                RemoteObservation(catalog_observed=True),
            )
            self.assertTrue(plan.create)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_CREATE_DRAFT)
            failed = plan_publish(
                manuscript,
                CommandMode(MODE_DRY_RUN),
                RemoteObservation(catalog_observed=True),
            )
            self.assertEqual(failed.halt_reason, HALT_NO_SEARCH_HIT)
            self.assertEqual(failed.chapter_actions, ())
            self.assertFalse(failed.create)


class SchedulePlanTest(unittest.TestCase):
    def test_bound_draft_not_in_catalog_gets_next_morning_slot(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            first = manuscript.chapters[0]
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            manuscript.profile.set_binding(1, "c1", first.fingerprint, VISIBILITY_DRAFT)
            frozen = datetime(2026, 8, 31, 14, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(catalog_observed=True),
                )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].chapter_id, "c1")
            self.assertEqual(plan.chapter_actions[0].scheduled_at, "2026-09-01 08:00")

    def test_two_new_chapters_take_morning_then_afternoon(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (1, "工牌0727", "澄江市。"),
                    (2, "档案先于报表", "档案室。"),
                ),
            )
            manuscript = load_manuscript(root)
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            frozen = datetime(2026, 8, 31, 14, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH, allow_create=True),
                    RemoteObservation(catalog_observed=True, created_this_run=True),
                )
            self.assertEqual(plan.chapter_actions[0].scheduled_at, "2026-09-01 08:00")
            self.assertEqual(plan.chapter_actions[1].scheduled_at, "2026-09-01 15:00")


if __name__ == "__main__":
    unittest.main()
