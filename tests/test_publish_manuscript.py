from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from publish.manuscript import (
    ManuscriptError,
    BookProfile,
    ChapterBinding,
    VISIBILITY_SCHEDULE,
    apply_publish_fields,
    init_profile,
    load_manuscript,
    load_profile,
    markdown_to_plain,
    parse_channel,
    parse_schedule_times,
    preview_publish_slots,
    save_profile,
    take_next_publish_slot,
    scan_chapters,
    split_category,
)


class MarkdownToPlainTest(unittest.TestCase):
    def test_strips_markup_keeps_dialogue(self) -> None:
        source = "她说：「**不要**」。\n\n[见](http://x) `留痕`。\n"
        self.assertEqual(markdown_to_plain(source), "她说：「不要」。\n\n见 留痕。")


class CategoryParseTest(unittest.TestCase):
    def test_channel_and_category(self) -> None:
        self.assertEqual(parse_channel("番茄小说 · 女频"), "女频")
        self.assertEqual(parse_channel("番茄小说 / 男频"), "男频")
        self.assertEqual(
            split_category("现代言情 / 职场婚恋（双主线：产业园区）"),
            ("现代言情", "职场婚恋"),
        )
        self.assertEqual(
            split_category("悬疑脑洞（主）／都市推理（辅），感情线不作主引擎"),
            ("悬疑脑洞", "都市推理"),
        )


class ManuscriptScanTest(unittest.TestCase):
    def test_skips_memo_and_reads_title_from_heading(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            volume = root / "卷一-测试"
            volume.mkdir()
            (root / "00-连载备忘.md").write_text("据 `outline/x.md` 撰写。\n女主：梁知夏，总监\n", encoding="utf-8")
            (root / "README.md").write_text("# 不是章节\n", encoding="utf-8")
            (volume / "第004章-过桥到期.md").write_text(
                "# 第4章 过桥到期\n\n赵岳的私信我没立刻回。\n",
                encoding="utf-8",
            )
            chapters = scan_chapters(root)
            self.assertEqual(len(chapters), 1)
            self.assertEqual(chapters[0].sequence, 4)
            self.assertEqual(chapters[0].title, "过桥到期")
            self.assertEqual(chapters[0].body, "赵岳的私信我没立刻回。")

    def test_duplicate_sequence_stops(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = root / "卷一"
            second = root / "卷二"
            first.mkdir()
            second.mkdir()
            first.joinpath("第001章-甲.md").write_text("# 第1章 甲\n\n甲正文。\n", encoding="utf-8")
            second.joinpath("第001章-乙.md").write_text("# 第1章 乙\n\n乙正文。\n", encoding="utf-8")
            with self.assertRaises(ManuscriptError):
                scan_chapters(root)


class ProfileInitTest(unittest.TestCase):
    def test_seeds_from_outline_and_memo(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            outline = root / "outline.md"
            outline.write_text(
                "# 《工牌不认婚约》大纲\n\n"
                "## 0. 元信息\n\n"
                "- 频道：番茄小说 · 女频\n"
                "- 分类：现代言情 / 职场婚恋（双主线）\n\n"
                "## 1. 封面简介 + 300字长简介\n\n"
                "### 封面简介（约 120 字）\n\n"
                "短简介一句。\n\n"
                "### 长简介（约 300 字）\n\n"
                "长简介一段。\n\n"
                "## 2. 核心矛盾\n\n"
                "后面不管。\n",
                encoding="utf-8",
            )
            volume = root / "卷一"
            volume.mkdir()
            volume.joinpath("第001章-工牌0727.md").write_text(
                "# 第1章 工牌0727\n\n澄江市。\n",
                encoding="utf-8",
            )
            (root / "00-连载备忘.md").write_text(
                "据 `outline.md` 撰写。\n\n- 女主：梁知夏，招商总监\n",
                encoding="utf-8",
            )
            (root / "封面.jpg").write_bytes(b"fake-image")
            profile = init_profile(root, outline_path=outline)
            self.assertEqual(profile.fields["作品名称"], "工牌不认婚约")
            self.assertEqual(profile.fields["频道"], "女频")
            self.assertEqual(profile.fields["分类"], "现代言情")
            self.assertEqual(profile.fields["子分类"], "职场婚恋")
            self.assertEqual(profile.fields["封面简介"], "短简介一句。")
            self.assertEqual(profile.fields["简介"], "长简介一段。")
            self.assertEqual(profile.fields["主角姓名"], "梁知夏")
            self.assertEqual(profile.fields["封面"], "封面.jpg")
            self.assertEqual(profile.chapter_visibility, "草稿")
            self.assertEqual(profile.missing_create_fields(root), [])
            loaded = load_manuscript(root)
            self.assertEqual(loaded.chapters[0].title, "工牌0727")
            with self.assertRaises(ManuscriptError):
                init_profile(root)

    def test_create_manuscript_allows_empty_chapters_init_still_requires_them(self) -> None:
        from publish.manuscript import create_manuscript

        with tempfile.TemporaryDirectory() as temp_dir:
            novel_root = Path(temp_dir) / "novel"
            created = create_manuscript(novel_root, "空书")
            loaded = load_manuscript(created)
            self.assertEqual(loaded.profile.field_text("作品名称"), "空书")
            self.assertEqual(loaded.chapters, ())
            with self.assertRaises(ManuscriptError):
                init_profile(created)
            with self.assertRaises(ManuscriptError):
                create_manuscript(novel_root, "")
            with self.assertRaises(ManuscriptError):
                create_manuscript(novel_root, "a/b")
            with self.assertRaises(ManuscriptError):
                create_manuscript(novel_root, "空书")

    def test_create_manuscript_fills_existing_dir_without_profile(self) -> None:
        from publish.manuscript import create_manuscript

        with tempfile.TemporaryDirectory() as temp_dir:
            novel_root = Path(temp_dir) / "novel"
            existing = novel_root / "旧稿"
            volume = existing / "卷一"
            volume.mkdir(parents=True)
            volume.joinpath("第001章-开篇.md").write_text(
                "开篇正文。\n", encoding="utf-8"
            )
            created = create_manuscript(novel_root, "旧稿")
            self.assertEqual(created, existing.resolve())
            loaded = load_manuscript(created)
            self.assertEqual(loaded.profile.field_text("作品名称"), "旧稿")
            self.assertEqual(len(loaded.chapters), 1)
            with self.assertRaises(ManuscriptError):
                create_manuscript(novel_root, "旧稿")

    def test_cover_rejects_path_outside_manuscript(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            volume = root / "卷一"
            volume.mkdir()
            volume.joinpath("第001章-工牌0727.md").write_text(
                "# 第1章 工牌0727\n\n澄江市。\n", encoding="utf-8"
            )
            (root / "封面.jpg").write_bytes(b"fake")
            profile = init_profile(root)
            profile.fields["封面"] = "../secret.png"
            self.assertIsNone(profile.cover_file(root))
            profile.fields["封面"] = "封面.jpg"
            self.assertEqual(profile.cover_file(root), (root / "封面.jpg").resolve())

    def test_invalid_chapter_binding_key_is_manuscript_error(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "书资料.yml"
            path.write_text(
                "绑定:\n  作品ID: ''\n  章节:\n    坏: {id: '1'}\n书资料:\n  作品名称: 甲\n",
                encoding="utf-8",
            )
            with self.assertRaises(ManuscriptError):
                load_profile(path)

    def test_rebind_clears_chapter_cache_and_same_id_keeps_it(self) -> None:
        profile = BookProfile(path=Path("x"), book_id="old")
        profile.set_binding(1, "c1", "abc", "草稿")
        self.assertFalse(profile.rebind("old"))
        self.assertEqual(profile.chapter_bindings[1].chapter_id, "c1")
        self.assertTrue(profile.rebind("new"))
        self.assertEqual(profile.book_id, "new")
        self.assertEqual(profile.chapter_bindings, {})
        self.assertTrue(profile.rebind(""))
        self.assertEqual(profile.book_id, "")

    def test_load_profile_reads_top_level_chapter_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "书资料.yml"
            path.write_text(
                "绑定:\n  作品ID: '99'\n章缓存:\n  1:\n    id: 'c1'\n    正文指纹: abcd\n"
                "    可见性: 定时发布\n    定时: '2026-09-01 08:00'\n书资料:\n  作品名称: 甲\n",
                encoding="utf-8",
            )
            profile = load_profile(path)
            self.assertEqual(profile.book_id, "99")
            self.assertEqual(profile.chapter_bindings[1].chapter_id, "c1")
            self.assertEqual(profile.chapter_bindings[1].fingerprint, "abcd")
            self.assertEqual(profile.chapter_bindings[1].scheduled_at, "2026-09-01 08:00")

    def test_primary_worktree_root_follows_gitdir(self) -> None:
        from publish.manuscript import primary_worktree_root

        with tempfile.TemporaryDirectory() as temp_dir:
            main = Path(temp_dir) / "Book"
            linked = Path(temp_dir) / "worktree"
            gitdir = main / ".git" / "worktrees" / "change"
            gitdir.mkdir(parents=True)
            linked.mkdir()
            (linked / ".git").write_text(f"gitdir: {gitdir}\n", encoding="utf-8")
            self.assertEqual(primary_worktree_root(linked), main)
            self.assertEqual(primary_worktree_root(main), main)


class RealManuscriptTest(unittest.TestCase):
    def test_gongpai_chapter_contract(self) -> None:
        root = Path(__file__).resolve().parents[1] / "novel" / "工牌不认婚约"
        if not root.is_dir():
            self.skipTest("本地没有《工牌不认婚约》稿本")
        chapters = scan_chapters(root)
        self.assertEqual(len(chapters), 360)
        self.assertEqual(chapters[0].sequence, 1)
        self.assertEqual(chapters[0].title, "工牌0727")
        self.assertEqual(chapters[-1].sequence, 360)


class ScheduleTimesTest(unittest.TestCase):
    def test_parses_clock_list(self) -> None:
        self.assertEqual(parse_schedule_times(["8:00", "15:00"]), ("08:00", "15:00"))
        self.assertEqual(parse_schedule_times("08:00、15:00"), ("08:00", "15:00"))
        self.assertEqual(parse_schedule_times([480, 900]), ("08:00", "15:00"))

    def test_rejects_invalid_clock(self) -> None:
        with self.assertRaises(ManuscriptError):
            parse_schedule_times(["25:00"])

    def test_first_unpublished_uses_next_morning_slot(self) -> None:
        now = datetime(2026, 8, 31, 14, 0)
        clocks = ("08:00", "15:00")
        first = take_next_publish_slot(now, clocks, None)
        self.assertEqual(first, datetime(2026, 9, 1, 8, 0))
        second = take_next_publish_slot(now, clocks, first)
        self.assertEqual(second, datetime(2026, 9, 1, 15, 0))
        third = take_next_publish_slot(now, clocks, second)
        self.assertEqual(third, datetime(2026, 9, 2, 8, 0))

    def test_before_morning_slot_uses_today(self) -> None:
        now = datetime(2026, 8, 31, 7, 0)
        first = take_next_publish_slot(now, ("08:00", "15:00"), None)
        self.assertEqual(first, datetime(2026, 8, 31, 8, 0))

    def test_past_occupied_slot_skips_to_future(self) -> None:
        now = datetime(2026, 8, 31, 14, 0)
        occupied = datetime(2026, 8, 1, 8, 0)
        nxt = take_next_publish_slot(now, ("08:00", "15:00"), occupied)
        self.assertEqual(nxt, datetime(2026, 8, 31, 15, 0))


class PublishSettingsTest(unittest.TestCase):
    def test_apply_schedule_and_limits(self) -> None:
        profile = BookProfile(path=Path("x"))
        apply_publish_fields(
            profile,
            {
                "章节可见性": "定时发布",
                "连载状态": "连载",
                "单次章数上限": "10",
                "章间隔秒": "2",
                "人工等待秒": "30",
                "发稿时刻": "08:00、15:00",
            },
        )
        self.assertEqual(profile.chapter_visibility, "定时发布")
        self.assertEqual(profile.schedule_times, ("08:00", "15:00"))
        self.assertEqual(profile.max_chapters_per_run, 10)
        self.assertEqual(profile.delay_seconds, 2.0)
        self.assertEqual(profile.human_wait_seconds, 30.0)

    def test_zero_delay_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "书资料.yml"
            profile = BookProfile(path=path, delay_seconds=0.0, fields={"作品名称": "甲"})
            save_profile(profile)
            loaded = load_profile(path)
            self.assertEqual(loaded.delay_seconds, 0.0)

    def test_schedule_requires_clocks(self) -> None:
        profile = BookProfile(path=Path("x"))
        with self.assertRaises(ManuscriptError):
            apply_publish_fields(profile, {"章节可见性": "定时发布", "发稿时刻": ""})

    def test_preview_follows_occupied_slots(self) -> None:
        profile = BookProfile(
            path=Path("x"),
            chapter_visibility=VISIBILITY_SCHEDULE,
            schedule_times=("08:00", "15:00"),
            chapter_bindings={
                7: ChapterBinding(
                    visibility=VISIBILITY_SCHEDULE,
                    scheduled_at="2026-09-01 08:00",
                )
            },
        )
        slots = preview_publish_slots(
            profile,
            now=datetime(2026, 8, 31, 14, 0),
            count=3,
        )
        self.assertEqual(
            slots,
            ("2026-09-01 15:00", "2026-09-02 08:00", "2026-09-02 15:00"),
        )

    def test_preview_follows_last_sequence_not_later_datetime(self) -> None:
        profile = BookProfile(
            path=Path("x"),
            chapter_visibility=VISIBILITY_SCHEDULE,
            schedule_times=("08:00", "15:00"),
            chapter_bindings={
                1: ChapterBinding(
                    visibility=VISIBILITY_SCHEDULE,
                    scheduled_at="2026-09-20 08:00",
                ),
                7: ChapterBinding(
                    visibility=VISIBILITY_SCHEDULE,
                    scheduled_at="2026-09-01 15:00",
                ),
            },
        )
        slots = preview_publish_slots(
            profile,
            now=datetime(2026, 8, 31, 14, 0),
            count=3,
        )
        self.assertEqual(
            slots,
            ("2026-09-02 08:00", "2026-09-02 15:00", "2026-09-03 08:00"),
        )


if __name__ == "__main__":
    unittest.main()
