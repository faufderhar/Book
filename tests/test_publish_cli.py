from __future__ import annotations

import unittest

from publish.cli import build_command_mode, build_parser
from publish.plan import MODE_DISCOVER, MODE_DRY_RUN, MODE_PUBLISH


class PublishCliTest(unittest.TestCase):
    def test_run_defaults_to_claim_without_create(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "novel/工牌不认婚约"])
        self.assertFalse(args.create)
        self.assertFalse(args.dry_run)
        mode = build_command_mode("run", dry_run=args.dry_run, allow_create=args.create)
        self.assertEqual(mode.kind, MODE_PUBLISH)
        self.assertFalse(mode.allow_create)

    def test_max_chapters_must_be_positive(self) -> None:
        from publish.cli import command_publish

        self.assertEqual(
            command_publish("novel/工牌不认婚约", dry_run=True, discover_only=False, max_chapters=0),
            2,
        )

    def test_run_create_passes_allow_create(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["run", "--create", "--dry-run", "novel/工牌不认婚约"])
        self.assertTrue(args.create)
        self.assertTrue(args.dry_run)
        mode = build_command_mode("run", dry_run=args.dry_run, allow_create=args.create)
        self.assertEqual(mode.kind, MODE_DRY_RUN)
        self.assertTrue(mode.allow_create)

    def test_discover_never_allows_create(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["discover", "novel/工牌不认婚约"])
        self.assertFalse(hasattr(args, "create") and args.create)
        mode = build_command_mode("discover", allow_create=True)
        self.assertEqual(mode.kind, MODE_DISCOVER)
        self.assertFalse(mode.allow_create)

    def test_help_mentions_default_claim_and_create_flag(self) -> None:
        parser = build_parser()
        help_text = parser.format_help()
        self.assertIn("默认认领", help_text)
        run_parser = None
        for action in parser._subparsers._group_actions:
            for name, subparser in action.choices.items():
                if name == "run":
                    run_parser = subparser
        self.assertIsNotNone(run_parser)
        run_help_text = run_parser.format_help()
        self.assertIn("--create", run_help_text)
        self.assertIn("认领", run_help_text)


if __name__ == "__main__":
    unittest.main()
