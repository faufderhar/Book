from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from publish.manuscript import (
    ManuscriptError,
    init_profile,
    load_manuscript,
    markdown_to_plain,
    parse_channel,
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


if __name__ == "__main__":
    unittest.main()
