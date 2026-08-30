from __future__ import annotations

import argparse
import sys
from pathlib import Path

from publish.manuscript import ManuscriptError, init_profile, load_manuscript


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="番茄发稿：把指定稿本目录对齐到作家后台")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="从大纲和备忘灌书资料，不打开浏览器")
    init.add_argument("directory", help="稿本目录，例如 novel/工牌不认婚约")
    init.add_argument("--outline", default=None, help="大纲路径；缺省则读 00-连载备忘.md 里的引用")
    init.add_argument("--force", action="store_true", help="覆盖已有书资料.yml")

    discover = sub.add_parser("discover", help="打开创建或设置页，把表单标签补进书资料")
    discover.add_argument("directory")

    run = sub.add_parser("run", help="登录作家后台，创建或对齐平台作品与章节")
    run.add_argument("directory")
    run.add_argument("--dry-run", action="store_true", help="登录并对照远端，不提交")
    run.add_argument("--max-chapters", type=int, default=None, help="覆盖书资料里的单次章数上限")

    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return command_init(args)
        if args.command == "discover":
            return command_publish(args.directory, dry_run=False, discover_only=True, max_chapters=None)
        if args.command == "run":
            return command_publish(
                args.directory,
                dry_run=args.dry_run,
                discover_only=False,
                max_chapters=args.max_chapters,
            )
    except ManuscriptError as error:
        print(error, file=sys.stderr)
        return 2
    return 1


def command_init(args: argparse.Namespace) -> int:
    outline = Path(args.outline) if args.outline else None
    profile = init_profile(Path(args.directory), outline_path=outline, force=args.force)
    missing = profile.missing_create_fields(Path(args.directory).expanduser().resolve())
    print(f"已写入 {profile.path}", flush=True)
    if missing:
        print("还缺：" + "、".join(missing) + "。补全后再 run。", flush=True)
        return 2
    print("书资料必填项已齐。下一步：python -m publish discover <目录> 对照后台表单。", flush=True)
    return 0


def command_publish(
    directory: str,
    dry_run: bool,
    discover_only: bool,
    max_chapters: int | None,
) -> int:
    try:
        from publish.writer import PublishHalt, run_publish
    except ImportError:
        print("需要 Playwright：pip install playwright && python -m playwright install chromium", file=sys.stderr)
        return 1
    manuscript = load_manuscript(Path(directory))
    if max_chapters is not None:
        manuscript.profile.max_chapters_per_run = max_chapters
    try:
        report = run_publish(manuscript, dry_run=dry_run, discover_only=discover_only)
    except PublishHalt as halted:
        print(halted, file=sys.stderr)
        return 2
    if report.halted or report.missing_fields:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
