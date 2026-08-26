from __future__ import annotations

import argparse
import sys

import uvicorn

from book.models import PLATFORM_FANQIE
from book.platforms.fanqie import FanqieCrawler
from book.store import Store
from book.web.app import create_app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="网文风向标：公开榜单题材热度")
    sub = parser.add_subparsers(dest="command", required=True)

    crawl = sub.add_parser("crawl", help="礼貌采集公开榜单")
    crawl.add_argument("platform", choices=[PLATFORM_FANQIE])
    crawl.add_argument("--list", dest="list_ids", action="append", default=None)

    serve = sub.add_parser("serve", help="本机看板，只绑 127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)

    args = parser.parse_args(argv)
    if args.command == "crawl":
        return run_crawl(args.platform, args.list_ids)
    if args.command == "serve":
        uvicorn.run(create_app(), host="127.0.0.1", port=args.port, log_level="info")
        return 0
    return 1


def run_crawl(platform: str, list_ids: list[str] | None) -> int:
    store = Store()
    if platform == PLATFORM_FANQIE:
        crawler = FanqieCrawler(store)
        try:
            crawler.crawl(list_ids=list_ids)
        finally:
            crawler.close()
            store.close()
        return 0
    print(f"未实现的平台: {platform}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
