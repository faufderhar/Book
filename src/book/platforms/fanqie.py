from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Iterable
from urllib.parse import urlparse

from book.fetch import PlatformHalted, PoliteClient
from book.fonts import decode_text, mapping_from_woff
from book.models import (
    HEAD_METRIC_READERS,
    MAX_ENTRIES_PER_LIST,
    PLATFORM_FANQIE,
    RANK_KIND_NEW,
    RANK_KIND_READ,
    SERIAL_FINISHED,
    SERIAL_ONGOING,
    PlatformHalt,
    RankEntry,
    RankList,
    Snapshot,
)
from book.paths import config_path
from book.store import Store

SHANGHAI = timezone(timedelta(hours=8))
RANK_PAGE = "https://fanqienovel.com/rank"
RANK_API = "https://fanqienovel.com/api/rank/category/list"
INITIAL_STATE_MARK = "window.__INITIAL_STATE__="
FONT_URL_RE = re.compile(r"src:\s*url\((['\"]?)(https://[^)'\"]+\.woff2)\1\)")
ALLOWED_FONT_HOST_SUFFIXES = (
    "fanqienovel.com",
    "byteimg.com",
    "bytecdn.com",
    "bytedance.com",
    "toutiao.com",
    "snssdk.com",
)


class FanqieCrawler:
    def __init__(self, store: Store, client: PoliteClient | None = None) -> None:
        self.store = store
        self.client = client or PoliteClient(PLATFORM_FANQIE)
        self._font_mapping: dict[int, str] | None = None

    def close(self) -> None:
        self.client.close()

    def crawl(self, list_ids: list[str] | None = None) -> str | None:
        captured_at = datetime.now(tz=SHANGHAI)
        catalog = load_catalog()
        snapshot_date = snapshot_date_from_state({}, captured_at)
        halted_reason: str | None = None
        try:
            page = self.client.get(RANK_PAGE, referer="https://fanqienovel.com/")
            state = parse_initial_state(page.text)
            catalog = self._refresh_catalog_from_state(state, catalog)
            snapshot_date = snapshot_date_from_state(state, captured_at)
            self._font_mapping = self._load_font_mapping(page.text)
            self.store.clear_halt(PLATFORM_FANQIE)
        except PlatformHalted as halted:
            halted_reason = halted.reason
            self.store.record_halt(
                PlatformHalt(platform=PLATFORM_FANQIE, reason=halted.reason, halted_at=captured_at)
            )
        except Exception as error:
            halted_reason = str(error)
            self.store.record_halt(
                PlatformHalt(platform=PLATFORM_FANQIE, reason=halted_reason, halted_at=captured_at)
            )

        selected = catalog
        if list_ids:
            wanted = set(list_ids)
            selected = [item for item in catalog if item.list_id in wanted]

        for rank_list in selected:
            self.store.upsert_rank_list(rank_list)
            if halted_reason:
                self.store.mark_missing(
                    PLATFORM_FANQIE, rank_list.list_id, snapshot_date, captured_at, halted_reason
                )
                print(f"{rank_list.list_id} 失败 {halted_reason}", flush=True)
                continue
            try:
                entries = self._fetch_list(rank_list)
                self.store.replace_snapshot(
                    Snapshot(
                        platform=PLATFORM_FANQIE,
                        list_id=rank_list.list_id,
                        snapshot_date=snapshot_date,
                        captured_at=captured_at,
                        entries=entries,
                    )
                )
                print(f"{rank_list.list_id} {len(entries)}", flush=True)
            except PlatformHalted as halted:
                halted_reason = halted.reason
                self.store.record_halt(
                    PlatformHalt(platform=PLATFORM_FANQIE, reason=halted.reason, halted_at=captured_at)
                )
                self.store.mark_missing(
                    PLATFORM_FANQIE, rank_list.list_id, snapshot_date, captured_at, halted.reason
                )
                print(f"{rank_list.list_id} 失败 {halted.reason}", flush=True)
            except Exception as error:
                self.store.mark_missing(
                    PLATFORM_FANQIE, rank_list.list_id, snapshot_date, captured_at, str(error)
                )
                print(f"{rank_list.list_id} 失败 {error}", flush=True)
        return halted_reason

    def _refresh_catalog_from_state(self, state: dict, catalog: list[RankList]) -> list[RankList]:
        categories = (state.get("rank") or {}).get("rankCategoryTypeList") or {}
        if not categories:
            return catalog
        rebuilt: list[RankList] = []
        for gender, channel, items in (("1", "male", categories.get("male") or []), ("0", "female", categories.get("female") or [])):
            for mold, kind in (("2", RANK_KIND_READ), ("1", RANK_KIND_NEW)):
                for item in items:
                    category_id = str(item["id"])
                    rebuilt.append(
                        RankList(
                            platform=PLATFORM_FANQIE,
                            list_id=f"{gender}_{mold}_{category_id}",
                            channel=channel,
                            rank_kind=kind,
                            category=str(item["name"]),
                            has_occupancy=False,
                        )
                    )
        if rebuilt:
            save_catalog(rebuilt)
            return rebuilt
        return catalog

    def _load_font_mapping(self, html: str) -> dict[int, str]:
        url = font_url_from_html(html)
        if not url or not font_host_allowed(url):
            return {}
        font = self.client.get(url, referer=RANK_PAGE)
        return mapping_from_woff(font.content)

    def _fetch_list(self, rank_list: RankList) -> tuple[RankEntry, ...]:
        gender, mold, category_id = rank_list.list_id.split("_", 2)
        page_url = f"{RANK_PAGE}/{rank_list.list_id}"
        api_url = (
            f"{RANK_API}?app_id=2503&rank_list_type=3&offset=0&limit={MAX_ENTRIES_PER_LIST}"
            f"&category_id={category_id}&rank_version=&gender={gender}&rankMold={mold}"
        )
        result = self.client.get(
            api_url,
            referer=page_url,
            headers={"Accept": "application/json, text/plain, */*"},
        )
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError as error:
            raise PlatformHalted(PLATFORM_FANQIE, f"风控或非JSON {rank_list.list_id}") from error
        if payload.get("code") != 0:
            raise RuntimeError(f"番茄榜单接口 code={payload.get('code')} {rank_list.list_id}")
        books = (payload.get("data") or {}).get("book_list") or []
        if not books:
            raise RuntimeError(f"番茄榜单空列表 {rank_list.list_id}")
        mapping = self._font_mapping or {}
        entries: list[RankEntry] = []
        seen_ranks: set[int] = set()
        for book in books[:MAX_ENTRIES_PER_LIST]:
            rank = int(book.get("currentPos") or 0)
            if rank <= 0 or rank in seen_ranks:
                continue
            work_id = str(book.get("bookId") or "")
            if not work_id:
                continue
            seen_ranks.add(rank)
            creation_status = str(book.get("creationStatus") or "")
            if creation_status == "0":
                serial_status = SERIAL_FINISHED
            elif creation_status == "1":
                serial_status = SERIAL_ONGOING
            else:
                serial_status = None
            metric_value = parse_int(book.get("read_count") or book.get("readCount"))
            updated_at = unix_to_text(book.get("lastChapterUpdateTime"))
            entries.append(
                RankEntry(
                    rank=rank,
                    work_id=work_id,
                    title=decode_text(str(book.get("bookName") or ""), mapping),
                    author=decode_text(str(book.get("author") or ""), mapping),
                    category=rank_list.category,
                    serial_status=serial_status,
                    metric_name=HEAD_METRIC_READERS,
                    metric_value=metric_value,
                    updated_at_on_page=updated_at,
                )
            )
        entries.sort(key=lambda item: item.rank)
        if not entries:
            raise RuntimeError(f"番茄榜单空列表 {rank_list.list_id}")
        return tuple(entries)


def load_catalog() -> list[RankList]:
    path = config_path("fanqie_lists.json")
    document = json.loads(path.read_text())
    return [
        RankList(
            platform=PLATFORM_FANQIE,
            list_id=item["list_id"],
            channel=item["channel"],
            rank_kind=item["rank_kind"],
            category=item["category"],
            has_occupancy=bool(item.get("has_occupancy")),
        )
        for item in document["lists"]
    ]


def save_catalog(lists: Iterable[RankList]) -> None:
    path = config_path("fanqie_lists.json")
    document = {
        "platform": PLATFORM_FANQIE,
        "page": RANK_PAGE,
        "lists": [
            {
                "list_id": item.list_id,
                "channel": item.channel,
                "rank_kind": item.rank_kind,
                "category_id": item.list_id.split("_")[-1],
                "category": item.category,
                "has_occupancy": item.has_occupancy,
            }
            for item in lists
        ],
    }
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n")


def parse_initial_state(html: str) -> dict:
    start = html.find(INITIAL_STATE_MARK)
    if start < 0:
        return {}
    raw = html[start + len(INITIAL_STATE_MARK) :]
    depth = 0
    end = None
    in_string = False
    escaped = False
    for index, character in enumerate(raw):
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
            continue
        if character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                end = index + 1
                break
    if end is None:
        return {}
    return json.loads(raw[:end])


def font_url_from_html(html: str) -> str | None:
    match = FONT_URL_RE.search(html)
    return match.group(2) if match else None


def font_host_allowed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith("." + suffix) for suffix in ALLOWED_FONT_HOST_SUFFIXES)


def snapshot_date_from_state(_state: dict, captured_at: datetime) -> date:
    # 官网：每天下午 3 点前更新截止到上一日。三点前仍是前日榜。
    local = captured_at.astimezone(SHANGHAI)
    if local.hour < 15:
        return local.date() - timedelta(days=2)
    return local.date() - timedelta(days=1)


def parse_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return None


def unix_to_text(value: object) -> str | None:
    if not value:
        return None
    try:
        moment = datetime.fromtimestamp(int(value), tz=SHANGHAI)
    except (TypeError, ValueError, OSError):
        return None
    return moment.strftime("%Y-%m-%d %H:%M")
