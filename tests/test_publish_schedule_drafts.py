from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from publish.manuscript import VISIBILITY_PUBLISH, VISIBILITY_SCHEDULE, load_manuscript
from publish.plan import (
    ACTION_CREATE_DRAFT,
    ACTION_UPDATE_DRAFT,
    MODE_PUBLISH,
    CommandMode,
    RemoteChapter,
    RemoteObservation,
    plan_publish,
)
from publish.writer import catalog_row_scheduled_at, catalog_row_status, unique_remote_chapters
from publish.manuscript import BookProfile, save_profile


def write_manuscript(
    root: Path,
    *,
    book_id: str = "",
    chapter_specs: tuple[tuple[int, str, str], ...] | None = None,
) -> BookProfile:
    volume = root / "卷一"
    volume.mkdir()
    specs = chapter_specs or ((1, "工牌0727", "澄江市。"),)
    for sequence, chapter_title, body in specs:
        (volume / f"第{sequence:03d}章-{chapter_title}.md").write_text(
            f"# 第{sequence}章 {chapter_title}\n\n{body}\n",
            encoding="utf-8",
        )
    profile = BookProfile(
        path=root / "书资料.yml",
        book_id=book_id,
        fields={
            "作品名称": "工牌不认婚约",
            "频道": "女频",
            "分类": "现代言情",
            "简介": "长简介一段。",
            "封面": "",
            "标签": [],
        },
    )
    save_profile(profile)
    return profile


class CatalogDraftVisibilityPlanTest(unittest.TestCase):
    def test_schedule_visibility_updates_catalog_drafts_below_watermark(self) -> None:
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
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            remotes = (
                RemoteChapter(
                    title="第1章 工牌0727",
                    chapter_id="c1",
                    published=False,
                    visibility="草稿",
                ),
                RemoteChapter(
                    title="第2章 档案先于报表",
                    chapter_id="c2",
                    published=False,
                    visibility="草稿",
                ),
            )
            frozen = datetime(2026, 8, 31, 16, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(remote_chapters=remotes, catalog_observed=True),
                )
            self.assertEqual(plan.watermark, 2)
            self.assertEqual([action.sequence for action in plan.chapter_actions], [1, 2, 3])
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[1].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[2].action, ACTION_CREATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].scheduled_at, "2026-09-01 08:00")
            self.assertEqual(plan.chapter_actions[1].scheduled_at, "2026-09-01 15:00")
            self.assertEqual(plan.chapter_actions[2].scheduled_at, "2026-09-02 08:00")

    def test_published_below_watermark_stays_untouched_when_scheduling(self) -> None:
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
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            remotes = (
                RemoteChapter(title="第1章 工牌0727", chapter_id="c1", published=True),
                RemoteChapter(
                    title="第2章 档案先于报表",
                    chapter_id="c2",
                    published=False,
                    visibility="草稿",
                ),
            )
            frozen = datetime(2026, 8, 31, 16, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(remote_chapters=remotes, catalog_observed=True),
                )
            self.assertEqual([action.sequence for action in plan.chapter_actions], [2, 3])
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].chapter_id, "c2")
            self.assertEqual(plan.chapter_actions[1].action, ACTION_CREATE_DRAFT)

    def test_already_scheduled_catalog_row_is_not_rescheduled(self) -> None:
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
            remotes = (
                RemoteChapter(
                    title="第1章 工牌0727",
                    chapter_id="c1",
                    published=False,
                    visibility=VISIBILITY_SCHEDULE,
                ),
            )
            frozen = datetime(2026, 8, 31, 16, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(remote_chapters=remotes, catalog_observed=True),
                )
            self.assertEqual(len(plan.chapter_actions), 1)
            self.assertEqual(plan.chapter_actions[0].sequence, 2)
            self.assertEqual(plan.chapter_actions[0].action, ACTION_CREATE_DRAFT)

    def test_empty_catalog_visibility_below_watermark_is_not_converted(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (8, "投促局口径", "口径。"),
                    (23, "过桥到期", "过桥。"),
                ),
            )
            manuscript = load_manuscript(root)
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            remotes = (
                RemoteChapter(title="第8章 投促局口径", published=False, visibility=""),
                RemoteChapter(
                    title="第22章 最后通牙",
                    published=False,
                    visibility=VISIBILITY_SCHEDULE,
                ),
            )
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=remotes, catalog_observed=True),
            )
            self.assertEqual(plan.watermark, 22)
            self.assertEqual([action.sequence for action in plan.chapter_actions], [23])
            self.assertEqual(plan.chapter_actions[0].action, ACTION_CREATE_DRAFT)

    def test_stale_cache_slots_are_ignored_when_converting_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            first = manuscript.chapters[0]
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            manuscript.profile.set_binding(
                1,
                "c1",
                first.fingerprint,
                VISIBILITY_SCHEDULE,
                "2026-09-09 08:00",
            )
            remotes = (
                RemoteChapter(
                    title="第1章 工牌0727",
                    chapter_id="c1",
                    published=False,
                    visibility="草稿",
                ),
            )
            frozen = datetime(2026, 8, 31, 16, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(remote_chapters=remotes, catalog_observed=True),
                )
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].scheduled_at, "2026-09-01 08:00")

    def test_immediate_publish_updates_catalog_drafts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(root, book_id="10001")
            manuscript = load_manuscript(root)
            manuscript.profile.chapter_visibility = VISIBILITY_PUBLISH
            remotes = (
                RemoteChapter(
                    title="第1章 工牌0727",
                    chapter_id="c1",
                    published=False,
                    visibility="草稿",
                ),
            )
            plan = plan_publish(
                manuscript,
                CommandMode(MODE_PUBLISH),
                RemoteObservation(remote_chapters=remotes, catalog_observed=True),
            )
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].scheduled_at, "")


    def test_new_chapter_follows_catalog_slot_not_stale_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (22, "晚宴误拍", "晚宴。"),
                    (23, "傍上董事长", "董事长。"),
                ),
            )
            manuscript = load_manuscript(root)
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            manuscript.profile.set_binding(
                1,
                "c1",
                "stale",
                VISIBILITY_SCHEDULE,
                "2026-09-09 08:00",
            )
            remotes = (
                RemoteChapter(
                    title="第22章 晚宴误拍",
                    chapter_id="c22",
                    published=False,
                    visibility=VISIBILITY_SCHEDULE,
                    scheduled_at="2026-09-08 15:00",
                ),
            )
            frozen = datetime(2026, 8, 31, 16, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(remote_chapters=remotes, catalog_observed=True),
                )
            self.assertEqual([action.sequence for action in plan.chapter_actions], [23])
            self.assertEqual(plan.chapter_actions[0].action, ACTION_CREATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].scheduled_at, "2026-09-09 08:00")

    def test_skipped_watermark_slot_is_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            write_manuscript(
                root,
                book_id="10001",
                chapter_specs=(
                    (22, "晚宴误拍", "晚宴。"),
                    (23, "傍上董事长", "董事长。"),
                ),
            )
            manuscript = load_manuscript(root)
            manuscript.profile.chapter_visibility = VISIBILITY_SCHEDULE
            manuscript.profile.schedule_times = ("08:00", "15:00")
            remotes = (
                RemoteChapter(
                    title="第22章 晚宴误拍",
                    chapter_id="c22",
                    published=False,
                    visibility=VISIBILITY_SCHEDULE,
                    scheduled_at="2026-09-08 15:00",
                ),
                RemoteChapter(
                    title="第23章 傍上董事长",
                    chapter_id="c23",
                    published=False,
                    visibility=VISIBILITY_SCHEDULE,
                    scheduled_at="2026-09-10 08:00",
                ),
            )
            frozen = datetime(2026, 8, 31, 16, 0)
            with patch("publish.plan.datetime") as mocked:
                mocked.now.return_value = frozen
                plan = plan_publish(
                    manuscript,
                    CommandMode(MODE_PUBLISH),
                    RemoteObservation(remote_chapters=remotes, catalog_observed=True),
                )
            self.assertEqual([action.sequence for action in plan.chapter_actions], [23])
            self.assertEqual(plan.chapter_actions[0].action, ACTION_UPDATE_DRAFT)
            self.assertEqual(plan.chapter_actions[0].chapter_id, "c23")
            self.assertEqual(plan.chapter_actions[0].scheduled_at, "2026-09-09 08:00")


class CatalogRowStatusTest(unittest.TestCase):
    def test_classifies_published_draft_and_schedule(self) -> None:
        self.assertEqual(catalog_row_status("第1章 工牌0727 已发布 3828字"), (True, "已发布"))
        self.assertEqual(catalog_row_status("第1章 工牌0727 草稿 3828字"), (False, "草稿"))
        self.assertEqual(
            catalog_row_status("第1章 工牌0727 定时发布 2026-09-01 08:00"),
            (False, "定时发布"),
        )
        self.assertEqual(
            catalog_row_status("第22章 晚宴误拍 3806 0 待发布 2026-09-08 15:00"),
            (False, "定时发布"),
        )
        self.assertEqual(catalog_row_status("第1章 工牌0727 3828字"), (False, ""))

    def test_catalog_row_scheduled_at_parses_pending_row(self) -> None:
        self.assertEqual(
            catalog_row_scheduled_at("第23章 傍上董事长 3291 0 待发布 2026-09-10 08:00"),
            "2026-09-10 08:00",
        )

    def test_unique_remote_chapters_prefers_scheduled_over_draft(self) -> None:
        remotes = unique_remote_chapters(
            [
                RemoteChapter(title="第1章 工牌0727", published=False, visibility="草稿"),
                RemoteChapter(
                    title="第1章 工牌0727",
                    published=False,
                    visibility="定时发布",
                    chapter_id="1",
                ),
            ]
        )
        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0].visibility, "定时发布")
        self.assertEqual(remotes[0].chapter_id, "1")

    def test_unique_remote_chapters_keeps_scheduled_at(self) -> None:
        remotes = unique_remote_chapters(
            [
                RemoteChapter(
                    title="第22章 晚宴误拍",
                    published=False,
                    visibility="定时发布",
                    chapter_id="c22",
                ),
                RemoteChapter(
                    title="第22章 晚宴误拍",
                    published=False,
                    visibility="定时发布",
                    chapter_id="c22",
                    scheduled_at="2026-09-08 15:00",
                ),
            ]
        )
        self.assertEqual(len(remotes), 1)
        self.assertEqual(remotes[0].scheduled_at, "2026-09-08 15:00")


if __name__ == "__main__":
    unittest.main()
