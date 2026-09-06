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
    ACTION_UPDATE_DRAFT,
    HALT_BOUND_BOOK_UNOPENABLE,
    HALT_CATALOG_MIDDLE_GAP,
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

    def test_similar_title_with_allow_create_creates(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            sequel = SearchHit(book_id="20002", row_text="工牌不认婚约续 连载")
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(search_hits=(sequel,)),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertTrue(plan.create)
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
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertTrue(plan.create)
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

    def test_empty_manuscript_can_create_and_writes_no_chapters(self) -> None:
        from publish.manuscript import create_manuscript

        with tempfile.TemporaryDirectory() as temp_dir:
            created = create_manuscript(Path(temp_dir), "空书")
            (created / "封面.jpg").write_bytes(b"cover")
            profile = load_manuscript(created).profile
            profile.fields.update(
                {
                    "频道": "女频",
                    "分类": "现代言情",
                    "简介": "简介",
                    "封面": "封面.jpg",
                }
            )
            save_profile(profile)
            manuscript = load_manuscript(created)
            claim = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(),
            )
            self.assertIsNone(claim.halt_reason)
            self.assertTrue(claim.create)
            chapters = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(
                    bound_book_openable=True,
                    catalog_observed=True,
                    created_this_run=True,
                ),
            )
            self.assertEqual(chapters.chapter_actions, ())


class SettingsPlanTest(unittest.TestCase):
    def test_empty_keys_are_not_written_back(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, fields={"主角姓名": "", "标签": []}, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(),
            )
            self.assertNotIn("主角姓名", plan.fields_to_write)
            self.assertNotIn("标签", plan.fields_to_write)
            self.assertNotIn("封面", plan.fields_to_write)
            self.assertEqual(plan.fields_to_write.get("作品名称"), "工牌不认婚约")

    def test_locked_fields_are_reported_without_halting(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(
                    locked_fields=("作品名称",),
                ),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertTrue(plan.create)
            self.assertEqual(plan.locked_fields, ("作品名称",))
            self.assertNotIn("作品名称", plan.fields_to_write)
            self.assertEqual(plan.fields_to_write.get("简介"), "长简介一段。")

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
            write_manuscript(root, cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            ongoing = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(),
            )
            self.assertNotIn("连载状态", ongoing.fields_to_write)
            manuscript.profile.serial_status = SERIAL_FINISHED
            finished = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH, allow_create=True),
                RemoteObservation(),
            )
            self.assertEqual(finished.fields_to_write.get("连载状态"), SERIAL_FINISHED)

    def test_publish_does_not_write_book_settings(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001", cover_name="封面.jpg")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(catalog_observed=True),
            )
            self.assertEqual(plan.fields_to_write, {})
            self.assertIsNone(plan.cover_to_upload)
            self.assertEqual(plan.empty_keys_to_add, ())


class ChapterPlanTest(unittest.TestCase):
    def test_watermark_skips_catalog_chapters_and_creates_after(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (1, "工牌0727", "澄江市。"),
                    (2, "档案先于报表", "档案室。"),
                    (3, "过桥到期", "过桥。"),
                ),
            )
            manuscript = load_manuscript(root)
            first = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True)
            second = RemoteChapter(title="第2章 另一标题", chapter_id="c2", published=False)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(first, second), catalog_observed=True),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.watermark, 2)
            self.assertEqual(len(plan.chapter_actions), 1)
            self.assertEqual(plan.chapter_actions[0].sequence, 3)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_CREATE_DRAFT)

    def test_cached_chapter_above_watermark_means_catalog_truncated(self) -> None:
        """章缓存记着第 2 章建过，目录却只读到第 1 章——这次没读全。

        放过去水位就偏低：后台已经有的章会被当成没建过而重复新建。
        """
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
            second = manuscript.chapters[1]
            manuscript.profile.cache_chapter(2, "c2", second.fingerprint, VISIBILITY_DRAFT)
            remote_first = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(remote_first,), catalog_observed=True),
            )
            self.assertEqual(plan.watermark, 1)
            self.assertIn("未确认水位", plan.halt_reason or "")
            self.assertIn("第2章", plan.halt_reason or "")
            self.assertEqual(plan.chapter_actions, ())

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
            self.assertEqual(plan.watermark, 9)
            self.assertEqual(plan.chapter_actions, ())

    def test_title_contained_in_earlier_chapter_does_not_steal_its_id(self) -> None:
        """本地《终局》不得认到「第9章 终局伏笔」，否则新章会覆盖第9章正文。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (9, "终局伏笔", "伏笔。"),
                    (10, "收网", "收网。"),
                    (11, "终局", "终局。"),
                ),
            )
            manuscript = load_manuscript(root)
            ninth = RemoteChapter(title="第9章 终局伏笔", chapter_id="c9", visibility=VISIBILITY_DRAFT)
            tenth = RemoteChapter(title="第10章 收网", chapter_id="c10", published=True)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(ninth, tenth), catalog_observed=True),
            )
            self.assertEqual(plan.watermark, 10)
            self.assertEqual(len(plan.chapter_actions), 1)
            action = plan.chapter_actions[0]
            self.assertEqual(action.sequence, 11)
            self.assertEqual(action.action, ACTION_CREATE_DRAFT)
            self.assertEqual(action.chapter_id, "")
            self.assertEqual(plan.extra_remote_chapters, ())

    def test_sequence_wins_over_a_longer_title_that_contains_it(self) -> None:
        """目录里同时有「第5章 开局前夜」和「第12章 开局」时，第12章只认后者。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=((5, "开局前夜", "前夜。"), (12, "开局", "开局。")),
            )
            manuscript = load_manuscript(root)
            fifth = RemoteChapter(title="第5章 开局前夜", chapter_id="c5", visibility=VISIBILITY_DRAFT)
            twelfth = RemoteChapter(title="第12章 开局", chapter_id="c12", visibility=VISIBILITY_DRAFT)
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(fifth, twelfth), catalog_observed=True),
            )
            self.assertEqual(plan.watermark, 12)
            converted = {action.sequence: action.chapter_id for action in plan.chapter_actions}
            self.assertEqual(converted, {5: "c5", 12: "c12"})

    def test_unnumbered_catalog_row_still_matches_by_title(self) -> None:
        """认不出序号的目录行，标题仍是唯一抓手。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=((1, "工牌0727", "澄江市。"), (2, "档案先于报表", "档案室。")),
            )
            manuscript = load_manuscript(root)
            numbered = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True)
            unnumbered = RemoteChapter(title="档案先于报表", chapter_id="cx", visibility=VISIBILITY_DRAFT)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(numbered, unnumbered), catalog_observed=True),
            )
            self.assertEqual(plan.watermark, 1)
            self.assertEqual(len(plan.chapter_actions), 1)
            self.assertEqual(plan.chapter_actions[0].sequence, 2)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].chapter_id, "cx")
            self.assertEqual(plan.extra_remote_chapters, ())

    def test_claimed_existing_empty_catalog_writes_from_first_chapter(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(catalog_observed=True),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_CREATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].sequence, 1)
            self.assertEqual(plan.book_id, "10001")
            self.assertEqual(plan.watermark, 0)

    def test_unobserved_catalog_does_not_create_chapters(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(catalog_observed=False),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.chapter_actions, ())
            self.assertEqual(plan.watermark, 0)

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

    def test_write_budget_starts_after_watermark(self) -> None:
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
                    (5, "述职不认婚", "五。"),
                ),
            )
            manuscript = load_manuscript(root)
            published = RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True)
            second = RemoteChapter(title="第2章 档案先于报表", chapter_id="c2", published=True)
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=(published, second), catalog_observed=True),
            )
            actions = {action.sequence: action.action for action in plan.chapter_actions}
            self.assertEqual(plan.watermark, 2)
            self.assertNotIn(1, actions)
            self.assertNotIn(2, actions)
            self.assertEqual(actions[3], ACTION_CREATE_DRAFT)
            self.assertEqual(actions[4], ACTION_CREATE_DRAFT)
            self.assertNotIn(5, actions)

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
    def test_bound_draft_missing_from_catalog_halts_instead_of_scheduling(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            first = manuscript.chapters[0]
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            manuscript.profile.cache_chapter(1, "c1", first.fingerprint, VISIBILITY_DRAFT)
            frozen = datetime(2026, 8, 31, 14, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(catalog_observed=True),
                )
            self.assertIn("未确认水位", plan.halt_reason or "")
            self.assertEqual(plan.chapter_actions, ())

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


class CatalogCoverageTest(unittest.TestCase):
    """水位必须够得到章缓存里最大的已建章，否则这次目录没读全。"""

    def _manuscript_with_cache(self, root: Path, cached: dict[int, str]):
        write_manuscript(
            root,
            book_id="10001",
            chapter_specs=(
                (1, "工牌0727", "澄江市。"),
                (2, "档案先于报表", "档案室。"),
                (3, "春衣短三十套", "保安军。"),
            ),
        )
        manuscript = load_manuscript(root)
        for sequence, chapter_id in cached.items():
            manuscript.profile.cache_chapter(sequence, chapter_id, "fp", VISIBILITY_DRAFT)
        return manuscript

    def test_watermark_covering_cache_does_not_halt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript_with_cache(Path(temp_dir), {1: "c1", 2: "c2"})
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1"),
                RemoteChapter(title="第2章 档案先于报表", chapter_id="c2"),
            )
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=remotes, catalog_observed=True),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.watermark, 2)

    def test_cache_without_chapter_ids_never_halts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript_with_cache(Path(temp_dir), {1: "", 2: ""})
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(catalog_observed=True),
            )
            self.assertIsNone(plan.halt_reason)

    def test_brand_new_book_with_empty_catalog_does_not_halt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript_with_cache(Path(temp_dir), {})
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(catalog_observed=True),
            )
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(plan.watermark, 0)
            self.assertEqual(
                [action.sequence for action in plan.chapter_actions],
                [1, 2, 3],
            )

    def test_truncated_catalog_halts_before_recreating_chapters(self) -> None:
        """《元丰勘合》真实故障：目录读空、缓存记着建过第 1、2 章，
        旧行为会把后台已有的第 3、4 章重新建一遍。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript_with_cache(Path(temp_dir), {1: "c1", 2: "c2"})
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(catalog_observed=True),
            )
            self.assertIn("未确认水位", plan.halt_reason or "")
            self.assertEqual(plan.chapter_actions, ())


class BackendWinsTest(unittest.TestCase):
    """整本读全之后后台就是事实：缺的补建，多出来的 ID 记回来。

    读全之前不能这么判——「目录里没有」那时只说明没读到。
    所以每条规则都拿 catalog_complete 分了两种情形各测一次。
    """

    def _manuscript(self, root: Path, cached: dict[int, str] | None = None):
        write_manuscript(
            root,
            book_id="10001",
            chapter_specs=(
                (1, "工牌0727", "澄江市。"),
                (2, "档案先于报表", "档案室。"),
                (3, "春衣短三十套", "保安军。"),
                (4, "午时第一次倒追", "北墙。"),
            ),
        )
        manuscript = load_manuscript(root)
        for sequence, chapter_id in (cached or {}).items():
            manuscript.profile.cache_chapter(sequence, chapter_id, "fp", VISIBILITY_DRAFT)
        return manuscript

    def _plan(self, manuscript, remotes, *, complete: bool):
        return plan_publish(
            manuscript,
            CommandMode(MODE_PUBLISH),
            RemoteObservation(
                remote_chapters=remotes,
                catalog_observed=True,
                catalog_complete=complete,
            ),
        )

    def test_trailing_gap_is_rebuilt_even_though_the_cache_has_an_id(self) -> None:
        """《元丰勘合》的真实处境：缓存记着 20 章全带 ID，后台只剩前几章。

        旧行为会拿那个旧 ID 去「更新草稿」，执行时找不到章而停机；
        以后台为准之后应当按新建处理。
        """
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(
                Path(temp_dir), {1: "c1", 2: "c2", 3: "c3", 4: "c4"}
            )
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
                RemoteChapter(title="第2章 档案先于报表", chapter_id="c2", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=True)
            self.assertIsNone(plan.halt_reason)
            actions = {action.sequence: action for action in plan.chapter_actions}
            self.assertEqual(actions[3].action, ACTION_CREATE_DRAFT)
            self.assertEqual(actions[4].action, ACTION_CREATE_DRAFT)
            self.assertEqual(actions[3].chapter_id, "")

    def test_same_gap_is_not_rebuilt_when_the_catalog_was_not_read_whole(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(
                Path(temp_dir), {1: "c1", 2: "c2", 3: "c3", 4: "c4"}
            )
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
                RemoteChapter(title="第2章 档案先于报表", chapter_id="c2", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=False)
            self.assertIsNotNone(plan.halt_reason)
            self.assertIn("未确认水位", plan.halt_reason or "")
            self.assertEqual(plan.chapter_actions, ())

    def test_middle_gap_halts_and_lists_the_missing_chapters(self) -> None:
        """后台只能在末尾追加，中间缺的章补进去会排到最后一章之后。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(Path(temp_dir))
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
                RemoteChapter(title="第4章 午时第一次倒追", chapter_id="c4", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=True)
            self.assertIsNotNone(plan.halt_reason)
            self.assertIn(HALT_CATALOG_MIDDLE_GAP, plan.halt_reason or "")
            self.assertIn("第2章", plan.halt_reason or "")
            self.assertIn("第3章", plan.halt_reason or "")
            self.assertEqual(plan.chapter_actions, ())

    def test_trailing_gap_alone_does_not_halt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(Path(temp_dir))
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
                RemoteChapter(title="第2章 档案先于报表", chapter_id="c2", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=True)
            self.assertIsNone(plan.halt_reason)

    def test_backfills_chapter_ids_the_cache_is_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(Path(temp_dir), {1: ""})
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
                RemoteChapter(title="第2章 档案先于报表", chapter_id="c2", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=True)
            self.assertIn((1, "c1"), plan.chapter_ids_to_cache)
            self.assertIn((2, "c2"), plan.chapter_ids_to_cache)

    def test_no_backfill_when_the_cache_already_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(Path(temp_dir), {1: "c1", 2: "c2"})
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
                RemoteChapter(title="第2章 档案先于报表", chapter_id="c2", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=True)
            self.assertEqual(plan.chapter_ids_to_cache, ())

    def test_backfill_never_invents_an_id_for_a_chapter_the_backend_lacks(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(Path(temp_dir))
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=True)
            cached_sequences = [sequence for sequence, _ in plan.chapter_ids_to_cache]
            self.assertNotIn(3, cached_sequences)
            self.assertNotIn(4, cached_sequences)

    def test_brand_new_book_reads_as_all_trailing_gap(self) -> None:
        """空新书：后台一章都没有，四章全是末尾缺口，不该报成中间缺口。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(Path(temp_dir))
            plan = self._plan(manuscript, (), complete=True)
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(len(plan.chapter_actions), 4)
            self.assertTrue(
                all(action.action == ACTION_CREATE_DRAFT for action in plan.chapter_actions)
            )

    def test_yuanfeng_trailing_gap_rebuilds_chapters_seven_through_twenty(self) -> None:
        """《元丰勘合》：本地 20 章全带 ID，后台只剩 1–6，整本读全后补建 7–20。"""
        with tempfile.TemporaryDirectory() as temp_dir:
            specs = tuple(
                (sequence, f"章{sequence}", f"正文{sequence}。")
                for sequence in range(1, 21)
            )
            write_manuscript(Path(temp_dir), book_id="10001", chapter_specs=specs)
            manuscript = load_manuscript(Path(temp_dir))
            for sequence in range(1, 21):
                manuscript.profile.cache_chapter(
                    sequence, f"c{sequence}", "fp", VISIBILITY_DRAFT
                )
            remotes = tuple(
                RemoteChapter(
                    title=f"第{sequence}章 章{sequence}",
                    chapter_id=f"c{sequence}",
                    published=False,
                    visibility=VISIBILITY_DRAFT,
                )
                for sequence in range(1, 7)
            )
            plan = self._plan(manuscript, remotes, complete=True)
            created = [
                action.sequence
                for action in plan.chapter_actions
                if action.action == ACTION_CREATE_DRAFT
            ]
            self.assertIsNone(plan.halt_reason)
            self.assertEqual(created, list(range(7, 21)))

    def test_trailing_rebuild_counts_against_the_run_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            manuscript = self._manuscript(Path(temp_dir))
            manuscript.profile.max_chapters_per_run = 1
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
            )
            plan = self._plan(manuscript, remotes, complete=True)
            created = [
                action.sequence
                for action in plan.chapter_actions
                if action.action == ACTION_CREATE_DRAFT
            ]
            self.assertEqual(created, [2])


class PartialPageReadIsCaughtDownstreamTest(unittest.TestCase):
    """分页控件没渲染时 writer 只能读到倒序第一页（最高那几章）。

    标成读全之后，缺的早期章落在水位之下，按中间缺口停机，不会拿去补建。
    """

    def test_first_page_only_halts_as_a_middle_gap_not_a_rebuild(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            write_manuscript(
                Path(temp_dir),
                book_id="10001",
                chapter_specs=tuple(
                    (sequence, f"章{sequence}", f"正文{sequence}。")
                    for sequence in (1, 2, 92)
                ),
            )
            manuscript = load_manuscript(Path(temp_dir))
            remotes = tuple(
                RemoteChapter(
                    title=f"第{sequence}章 章{sequence}",
                    chapter_id=f"c{sequence}",
                    published=True,
                )
                for sequence in range(78, 93)
            )
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(
                    remote_chapters=remotes,
                    catalog_observed=True,
                    catalog_complete=True,
                ),
            )
            self.assertIn(HALT_CATALOG_MIDDLE_GAP, plan.halt_reason or "")
            self.assertIn("第1章", plan.halt_reason or "")
            self.assertIn("第2章", plan.halt_reason or "")
            self.assertEqual(plan.chapter_actions, ())


if __name__ == "__main__":
    unittest.main()
