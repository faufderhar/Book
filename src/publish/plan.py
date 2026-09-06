from __future__ import annotations

import re
from datetime import datetime
from dataclasses import dataclass, field

from publish.manuscript import (
    REQUIRED_CREATE_FIELDS,
    SERIAL_FINISHED,
    VISIBILITY_DRAFT,
    VISIBILITY_PUBLISH,
    VISIBILITY_SCHEDULE,
    BookProfile,
    Chapter,
    ChapterCache,
    Manuscript,
    find_cover_name,
    format_scheduled_at,
    latest_occupied_slot,
    parse_scheduled_at,
    take_next_publish_slot,
)

MODE_PUBLISH = "发稿"
MODE_DRY_RUN = "干跑"
MODE_DISCOVER = "对照表单"

HALT_NO_SEARCH_HIT = "搜索没有命中平台作品，未创建"
HALT_MANY_SEARCH_HITS = "搜索命中多本平台作品"
HALT_BOUND_BOOK_UNOPENABLE = "已绑定平台作品打不开，未创建"
HALT_MISSING_CREATE_FIELDS = "创建平台作品前书资料不完整"
HALT_CATALOG_BELOW_CACHE = "后台目录没读全，未确认水位"
HALT_CATALOG_MIDDLE_GAP = "后台缺的章夹在中间，没有自动补建"

ACTION_SKIP = "跳过"
ACTION_CREATE_DRAFT = "新建草稿"
ACTION_UPDATE_DRAFT = "更新草稿"
ACTION_UPDATE_VISIBILITY = "改可见性"
ACTION_PUBLISHED_MISMATCH = "已发布不一致"

_STATUS_SUFFIXES = ("已签约", "未签约", "已完结", "连载", "完结")
_AFTER_TITLE_NOISE = " \t·|-—/／"
CHAPTER_NUMBER_RE = re.compile(r"第0*(\d+)章")


@dataclass(frozen=True)
class CommandMode:
    kind: str
    allow_create: bool = False


@dataclass(frozen=True)
class SearchHit:
    book_id: str
    row_text: str
    work_name: str = ""


@dataclass(frozen=True)
class RemoteChapter:
    title: str
    chapter_id: str = ""
    published: bool = False
    fingerprint: str = ""
    visibility: str = ""
    scheduled_at: str = ""


@dataclass(frozen=True)
class RemoteObservation:
    search_hits: tuple[SearchHit, ...] = ()
    bound_book_openable: bool = True
    remote_chapters: tuple[RemoteChapter, ...] = ()
    form_labels: tuple[str, ...] = ()
    locked_fields: tuple[str, ...] = ()
    catalog_observed: bool = False
    catalog_complete: bool = False
    created_this_run: bool = False


@dataclass(frozen=True)
class ChapterAction:
    sequence: int
    action: str
    chapter_id: str = ""
    reason: str = ""
    scheduled_at: str = ""


@dataclass(frozen=True)
class ClaimDecision:
    book_id: str = ""
    create: bool = False
    halt_reason: str | None = None
    candidates: tuple[SearchHit, ...] = ()
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class SettingsDecision:
    fields_to_write: dict[str, object] = field(default_factory=dict)
    cover_to_upload: str | None = None
    empty_keys_to_add: tuple[str, ...] = ()
    locked_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChaptersDecision:
    actions: tuple[ChapterAction, ...] = ()
    extra_remote_chapters: tuple[RemoteChapter, ...] = ()
    halt_reason: str | None = None
    watermark: int = 0
    anchor_scheduled_at: str = ""
    chapter_ids_to_cache: tuple[tuple[int, str], ...] = ()


@dataclass(frozen=True)
class PublishPlan:
    halt_reason: str | None = None
    book_id: str = ""
    create: bool = False
    fields_to_write: dict[str, object] = field(default_factory=dict)
    cover_to_upload: str | None = None
    empty_keys_to_add: tuple[str, ...] = ()
    chapter_actions: tuple[ChapterAction, ...] = ()
    extra_remote_chapters: tuple[RemoteChapter, ...] = ()
    candidates: tuple[SearchHit, ...] = ()
    locked_fields: tuple[str, ...] = ()
    missing_fields: tuple[str, ...] = ()
    watermark: int = 0
    anchor_scheduled_at: str = ""
    chapter_ids_to_cache: tuple[tuple[int, str], ...] = ()


def plan_publish(
    manuscript: Manuscript,
    mode: CommandMode,
    observation: RemoteObservation,
) -> PublishPlan:
    claim = decide_claim(manuscript, mode, observation)
    if claim.halt_reason:
        return PublishPlan(
            halt_reason=claim.halt_reason,
            create=False,
            candidates=claim.candidates,
            missing_fields=claim.missing_fields,
        )
    settings = decide_settings(manuscript, mode, observation, claim)
    chapters = decide_chapters(manuscript, mode, observation, claim)
    return PublishPlan(
        halt_reason=chapters.halt_reason,
        book_id=claim.book_id,
        create=claim.create,
        fields_to_write=dict(settings.fields_to_write),
        cover_to_upload=settings.cover_to_upload,
        empty_keys_to_add=settings.empty_keys_to_add,
        chapter_actions=chapters.actions,
        extra_remote_chapters=chapters.extra_remote_chapters,
        candidates=claim.candidates,
        locked_fields=settings.locked_fields,
        missing_fields=settings.missing_fields,
        watermark=chapters.watermark,
        anchor_scheduled_at=chapters.anchor_scheduled_at,
        chapter_ids_to_cache=chapters.chapter_ids_to_cache,
    )


def decide_claim(
    manuscript: Manuscript,
    mode: CommandMode,
    observation: RemoteObservation,
) -> ClaimDecision:
    bound_book_id = manuscript.profile.book_id.strip()
    if bound_book_id:
        if not observation.bound_book_openable:
            return ClaimDecision(halt_reason=HALT_BOUND_BOOK_UNOPENABLE, create=False)
        return ClaimDecision(book_id=bound_book_id, create=False)

    work_title = manuscript.profile.field_text("作品名称")
    matching_hits = _matching_hits(work_title, observation.search_hits)
    unique_work_names = _unique_work_names(work_title, matching_hits)
    if len(unique_work_names) == 1 and unique_work_names[0] == work_title:
        book_ids = {hit.book_id for hit in matching_hits if hit.book_id}
        if len(book_ids) > 1:
            return ClaimDecision(
                halt_reason=HALT_MANY_SEARCH_HITS,
                create=False,
                candidates=matching_hits,
            )
        return ClaimDecision(book_id=matching_hits[0].book_id, create=False)
    if len(unique_work_names) > 1:
        return ClaimDecision(
            halt_reason=HALT_MANY_SEARCH_HITS,
            create=False,
            candidates=matching_hits,
        )
    if mode.kind == MODE_DISCOVER or not mode.allow_create:
        return ClaimDecision(halt_reason=HALT_NO_SEARCH_HIT, create=False)
    missing_fields = _missing_create_fields(manuscript)
    if missing_fields:
        return ClaimDecision(
            halt_reason=HALT_MISSING_CREATE_FIELDS,
            create=False,
            missing_fields=missing_fields,
        )
    return ClaimDecision(create=True)


def decide_settings(
    manuscript: Manuscript,
    mode: CommandMode,
    observation: RemoteObservation,
    claim: ClaimDecision,
) -> SettingsDecision:
    if claim.halt_reason:
        return SettingsDecision()
    profile = manuscript.profile
    locked_fields = tuple(dict.fromkeys(observation.locked_fields))
    known_keys = set(profile.fields)
    empty_keys_to_add = tuple(
        label for label in observation.form_labels if label and label not in known_keys
    )
    if mode.kind == MODE_DISCOVER:
        return SettingsDecision(
            empty_keys_to_add=empty_keys_to_add,
            locked_fields=locked_fields,
        )
    if not claim.create:
        return SettingsDecision()
    locked_set = set(locked_fields)
    fields_to_write: dict[str, object] = {}
    for key, raw_value in profile.fields.items():
        if key == "封面" or key in locked_set:
            continue
        if key == "标签":
            tags = profile.tag_list()
            if tags:
                fields_to_write[key] = tags
            continue
        text = profile.field_text(key)
        if text:
            fields_to_write[key] = raw_value if isinstance(raw_value, list) else text
    if profile.serial_status == SERIAL_FINISHED and "连载状态" not in locked_set:
        fields_to_write["连载状态"] = SERIAL_FINISHED
    cover_to_upload = _resolved_cover_name(manuscript) or None
    return SettingsDecision(
        fields_to_write=fields_to_write,
        cover_to_upload=cover_to_upload,
        empty_keys_to_add=empty_keys_to_add,
        locked_fields=locked_fields,
    )


def decide_chapters(
    manuscript: Manuscript,
    mode: CommandMode,
    observation: RemoteObservation,
    claim: ClaimDecision,
) -> ChaptersDecision:
    if claim.halt_reason or mode.kind == MODE_DISCOVER or not observation.catalog_observed:
        return ChaptersDecision()
    remotes = observation.remote_chapters
    watermark = catalog_watermark(remotes)
    complete = observation.catalog_complete
    if not complete:
        # 没读全整本时退回旧护栏：水位偏低可能只是没读到，不能当成后台缺章。
        incomplete = catalog_misses_cached_chapters(manuscript.profile, watermark)
        if incomplete:
            return ChaptersDecision(halt_reason=incomplete, watermark=watermark)
    else:
        middle = middle_gap_sequences(manuscript, remotes)
        if middle:
            return ChaptersDecision(
                halt_reason=describe_middle_gap(middle),
                watermark=watermark,
            )
    matched_indexes: set[int] = set()
    actions: list[ChapterAction] = []
    write_used = 0
    write_budget = manuscript.profile.max_chapters_per_run
    converting = sequences_to_apply_visibility(manuscript, remotes, watermark)
    for chapter in manuscript.chapters:
        remote_index, remote = _match_remote_chapter(
            chapter,
            remotes,
            manuscript.profile.chapter_cache.get(chapter.sequence),
        )
        if remote_index is not None:
            matched_indexes.add(remote_index)
        if chapter.sequence <= watermark:
            if chapter.sequence not in converting:
                continue
            if write_used >= write_budget:
                continue
            actions.append(
                ChapterAction(
                    sequence=chapter.sequence,
                    action=ACTION_UPDATE_VISIBILITY,
                    chapter_id=remote.chapter_id if remote is not None else "",
                    scheduled_at=next_write_slot(
                        manuscript.profile,
                        actions,
                        skip_sequences=converting,
                        remotes=remotes,
                        before_sequence=chapter.sequence,
                    ),
                )
            )
            write_used += 1
            continue
        if remote is not None and remote.published:
            titles_match = chapter.title in remote.title or remote.title in chapter.title
            if titles_match:
                actions.append(
                    ChapterAction(
                        sequence=chapter.sequence,
                        action=ACTION_SKIP,
                        chapter_id=remote.chapter_id,
                    )
                )
            else:
                actions.append(
                    ChapterAction(
                        sequence=chapter.sequence,
                        action=ACTION_PUBLISHED_MISMATCH,
                        chapter_id=remote.chapter_id,
                        reason=f"第{chapter.sequence}章 本地《{chapter.title}》 / 远端「{remote.title}」",
                    )
                )
            continue
        cached = manuscript.profile.chapter_cache.get(chapter.sequence)
        cached_id = cached.chapter_id if cached is not None else ""
        if write_used >= write_budget:
            continue
        scheduled_at = next_write_slot(
            manuscript.profile,
            actions,
            skip_sequences=converting,
            remotes=remotes,
            before_sequence=chapter.sequence,
        )
        # 整本已经读全时，目录里没有就是后台真没有——章缓存里那个 ID 是旧的，
        # 拿它去更新会找不到章。以后台为准，按新建处理。
        if remote is None and (complete or not cached_id):
            actions.append(
                ChapterAction(
                    sequence=chapter.sequence,
                    action=ACTION_CREATE_DRAFT,
                    scheduled_at=scheduled_at,
                )
            )
        else:
            actions.append(
                ChapterAction(
                    sequence=chapter.sequence,
                    action=ACTION_UPDATE_DRAFT,
                    chapter_id=remote.chapter_id if remote is not None else cached_id,
                    scheduled_at=scheduled_at,
                )
            )
        write_used += 1
    extra_remote_chapters = tuple(
        remote for index, remote in enumerate(remotes) if index not in matched_indexes
    )
    anchor = latest_catalog_slot(remotes)
    return ChaptersDecision(
        actions=tuple(actions),
        extra_remote_chapters=extra_remote_chapters,
        watermark=watermark,
        anchor_scheduled_at=format_scheduled_at(anchor) if anchor else "",
        chapter_ids_to_cache=chapter_ids_to_backfill(manuscript, remotes),
    )


def next_write_slot(
    profile: BookProfile,
    actions: list[ChapterAction],
    now: datetime | None = None,
    skip_sequences: set[int] | None = None,
    remotes: tuple[RemoteChapter, ...] = (),
    before_sequence: int | None = None,
) -> str:
    """水位最后一章的定时为锚，本轮新章按发稿时刻顺延。"""
    if profile.chapter_visibility != VISIBILITY_SCHEDULE:
        return ""
    clocks = profile.schedule_times
    if not clocks:
        return ""
    moment = now or datetime.now()
    occupied = latest_catalog_slot(remotes, before_sequence)
    if occupied is None:
        occupied = latest_occupied_slot(profile, skip_sequences)
    for action in actions:
        if before_sequence is not None and action.sequence >= before_sequence:
            continue
        current = parse_scheduled_at(action.scheduled_at)
        if current is None:
            continue
        if occupied is None or current > occupied:
            occupied = current
    return format_scheduled_at(take_next_publish_slot(moment, clocks, occupied))


def latest_catalog_slot(
    remotes: tuple[RemoteChapter, ...],
    before_sequence: int | None = None,
) -> datetime | None:
    """目录里序号最大、且带定时的那一章。发稿以它为锚顺延。"""
    latest_sequence = -1
    latest: datetime | None = None
    for remote in remotes:
        sequence = remote_chapter_number(remote)
        if sequence is None:
            continue
        if before_sequence is not None and sequence >= before_sequence:
            continue
        stamp = parse_scheduled_at(remote.scheduled_at)
        if stamp is None:
            continue
        if sequence > latest_sequence:
            latest_sequence = sequence
            latest = stamp
    return latest


def sequences_to_apply_visibility(
    manuscript: Manuscript,
    remotes: tuple[RemoteChapter, ...],
    watermark: int,
) -> set[int]:
    if manuscript.profile.chapter_visibility not in {VISIBILITY_SCHEDULE, VISIBILITY_PUBLISH}:
        return set()
    converting: set[int] = set()
    for chapter in manuscript.chapters:
        if chapter.sequence > watermark:
            continue
        _, remote = _match_remote_chapter(
            chapter,
            remotes,
            manuscript.profile.chapter_cache.get(chapter.sequence),
        )
        if remote is None or remote.published:
            continue
        if remote.visibility != VISIBILITY_DRAFT:
            continue
        converting.add(chapter.sequence)
    return converting


def _matching_hits(work_title: str, search_hits: tuple[SearchHit, ...]) -> tuple[SearchHit, ...]:
    if not work_title:
        return ()
    return tuple(hit for hit in search_hits if work_title in hit.row_text)


def _unique_work_names(work_title: str, matching_hits: tuple[SearchHit, ...]) -> tuple[str, ...]:
    unique_names: list[str] = []
    seen_names: set[str] = set()
    for hit in matching_hits:
        work_name = _canonical_work_name(hit, work_title)
        if work_name in seen_names:
            continue
        seen_names.add(work_name)
        unique_names.append(work_name)
    return tuple(unique_names)


def _canonical_work_name(hit: SearchHit, expected_title: str) -> str:
    if hit.work_name.strip():
        return hit.work_name.strip()
    return _work_name_from_row(hit.row_text, expected_title)


def _work_name_from_row(row_text: str, expected_title: str) -> str:
    if expected_title not in row_text:
        return " ".join(row_text.split()).strip()
    after_title = row_text.split(expected_title, 1)[1]
    leftover = after_title.strip(_AFTER_TITLE_NOISE)
    while leftover:
        matched_suffix = next(
            (suffix for suffix in _STATUS_SUFFIXES if leftover.startswith(suffix)),
            None,
        )
        if matched_suffix is None:
            extra_token = leftover.split()[0] if leftover.split() else leftover
            return expected_title + extra_token
        leftover = leftover[len(matched_suffix) :].strip(_AFTER_TITLE_NOISE)
    return expected_title


def is_exact_work_row(row_text: str, work_title: str) -> bool:
    if not work_title or work_title not in row_text:
        return False
    return _work_name_from_row(row_text, work_title) == work_title


def _missing_create_fields(manuscript: Manuscript) -> tuple[str, ...]:
    missing: list[str] = []
    for key in REQUIRED_CREATE_FIELDS:
        if key == "封面":
            if not _resolved_cover_name(manuscript):
                missing.append("封面")
            continue
        if not manuscript.profile.field_text(key):
            missing.append(key)
    return tuple(missing)


def _resolved_cover_name(manuscript: Manuscript) -> str:
    cover = manuscript.profile.cover_file(manuscript.directory)
    if cover is not None:
        relative = manuscript.profile.field_text("封面")
        if relative:
            return relative
        try:
            return str(cover.relative_to(manuscript.directory))
        except ValueError:
            return cover.name
    return find_cover_name(manuscript.directory)


def catalog_misses_cached_chapters(profile: BookProfile, watermark: int) -> str | None:
    """章缓存记着后台已经建过的章，水位却够不到它——这次目录没读全。

    放过去水位就偏低：缓存里没记 ID 的章会被当成没建过而重复新建，
    记了 ID 的章又会在执行时找不到，报成「找不到要更新的草稿」。

    只在观察没有覆盖整本时才用得上这条。目录是分页的，逐页遍历成功之后
    水位偏低就不再是"没读全"，而是后台真的少了章，由缺口补建处理。
    """
    created = [
        sequence
        for sequence, item in profile.chapter_cache.items()
        if item.chapter_id
    ]
    if not created:
        return None
    highest = max(created)
    if watermark >= highest:
        return None
    return (
        f"{HALT_CATALOG_BELOW_CACHE}：只读到第{watermark}章，"
        f"章缓存记着第{highest}章已经建过。目录是分页的，"
        "先确认每一页都读到了；整本读全之后后台真缺的章会自动补建，不必清章缓存。"
    )


def missing_remote_sequences(
    manuscript: Manuscript,
    remotes: tuple[RemoteChapter, ...],
) -> list[int]:
    """本地有、这份远端观察里却没有的章序号。

    只有在整本确实读全之后调用才有意义；没读全时"目录里没有"只说明没读到。
    """
    missing: list[int] = []
    for chapter in manuscript.chapters:
        _, remote = _match_remote_chapter(
            chapter,
            remotes,
            manuscript.profile.chapter_cache.get(chapter.sequence),
        )
        if remote is None:
            missing.append(chapter.sequence)
    return missing


def middle_gap_sequences(
    manuscript: Manuscript,
    remotes: tuple[RemoteChapter, ...],
) -> list[int]:
    """缺口里夹在后台已有章之间的那部分。

    后台只能在末尾追加章节。把中间缺的章补进去，它会排到最后一章之后，
    章号和实际顺序就对不上了。所以中间缺口只报告，不自动补。
    """
    watermark = catalog_watermark(remotes)
    return [
        sequence
        for sequence in missing_remote_sequences(manuscript, remotes)
        if sequence < watermark
    ]


def describe_middle_gap(sequences: list[int]) -> str:
    listed = "、".join(f"第{sequence}章" for sequence in sequences[:10])
    more = f"（共 {len(sequences)} 章，只列出前 10 章）" if len(sequences) > 10 else ""
    return (
        f"{HALT_CATALOG_MIDDLE_GAP}：后台缺 {listed}{more}，"
        "但它们后面还有别的章。后台只能在末尾追加，补进去会排到最后一章之后，"
        "请先在后台确认这几章的去向再决定怎么补。"
    )


def chapter_ids_to_backfill(
    manuscript: Manuscript,
    remotes: tuple[RemoteChapter, ...],
) -> tuple[tuple[int, str], ...]:
    """后台有章 ID、本地章缓存却缺记录或 ID 为空的章。

    以后台为准把 ID 记回来，下次就不会因为缓存里没 ID 而重复新建。

    这一条不受「整本是否读全」门控：记的都是这次真在目录里看见的章 ID，
    没读全只会让它少记几条，不会记错。补建才需要读全——那是从"没看见"
    推断"后台没有"，没读全就不成立。
    """
    backfill: list[tuple[int, str]] = []
    for chapter in manuscript.chapters:
        _, remote = _match_remote_chapter(
            chapter,
            remotes,
            manuscript.profile.chapter_cache.get(chapter.sequence),
        )
        if remote is None or not remote.chapter_id:
            continue
        cached = manuscript.profile.chapter_cache.get(chapter.sequence)
        if cached is not None and cached.chapter_id == remote.chapter_id:
            continue
        backfill.append((chapter.sequence, remote.chapter_id))
    return tuple(backfill)


def catalog_watermark(remotes: tuple[RemoteChapter, ...]) -> int:
    watermark = 0
    for remote in remotes:
        sequence = remote_chapter_number(remote)
        if sequence is None:
            continue
        watermark = max(watermark, sequence)
    return watermark


def _match_remote_chapter(
    chapter: Chapter,
    remotes: tuple[RemoteChapter, ...],
    cached: ChapterCache | None,
) -> tuple[int | None, RemoteChapter | None]:
    """章缓存里的章 ID 最准，其次认序号。标题只给认不出序号的目录行兜底。

    本地标题不带「第N章」，后台标题带；标题子串一旦压过序号，
    《终局》就会认到「第9章 终局伏笔」，把新章写进别人的正文里。
    """
    if cached and cached.chapter_id:
        for index, remote in enumerate(remotes):
            if remote.chapter_id == cached.chapter_id:
                return index, remote
    for index, remote in enumerate(remotes):
        if remote_chapter_number(remote) == chapter.sequence:
            return index, remote
    for index, remote in enumerate(remotes):
        if remote_chapter_number(remote) is not None:
            continue
        if chapter.title and chapter.title in remote.title:
            return index, remote
    return None, None


def remote_chapter_number(remote: RemoteChapter) -> int | None:
    numbered = CHAPTER_NUMBER_RE.search(remote.title)
    return int(numbered.group(1)) if numbered else None
