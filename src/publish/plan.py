from __future__ import annotations

from dataclasses import dataclass, field

from publish.manuscript import Manuscript

MODE_PUBLISH = "发稿"
MODE_DRY_RUN = "干跑"
MODE_DISCOVER = "对照表单"

HALT_NO_SEARCH_HIT = "搜索没有命中平台作品，未创建"
HALT_MANY_SEARCH_HITS = "搜索命中多本平台作品"
HALT_BOUND_BOOK_UNOPENABLE = "已绑定作品打不开，未创建"

_STATUS_SUFFIXES = ("已签约", "未签约", "已完结", "连载", "完结")
_AFTER_TITLE_NOISE = " \t·|-—/／"


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


@dataclass(frozen=True)
class RemoteObservation:
    search_hits: tuple[SearchHit, ...] = ()
    bound_book_openable: bool = True
    remote_chapters: tuple[RemoteChapter, ...] = ()
    form_labels: tuple[str, ...] = ()
    locked_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class ChapterAction:
    sequence: int
    action: str
    chapter_id: str = ""
    reason: str = ""


@dataclass(frozen=True)
class ClaimDecision:
    book_id: str = ""
    create: bool = False
    halt_reason: str | None = None
    candidates: tuple[SearchHit, ...] = ()


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


def plan_publish(
    manuscript: Manuscript,
    mode: CommandMode,
    observation: RemoteObservation,
) -> PublishPlan:
    claim = decide_claim(manuscript, mode, observation)
    settings = decide_settings(manuscript, mode, observation, claim)
    chapters = decide_chapters(manuscript, mode, observation, claim)
    halt_reason = claim.halt_reason or chapters.halt_reason
    return PublishPlan(
        halt_reason=halt_reason,
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
    if len(unique_work_names) == 1:
        if unique_work_names[0] != work_title:
            return ClaimDecision(halt_reason=HALT_NO_SEARCH_HIT, create=False)
        return ClaimDecision(book_id=matching_hits[0].book_id, create=False)
    if len(unique_work_names) > 1:
        return ClaimDecision(
            halt_reason=HALT_MANY_SEARCH_HITS,
            create=False,
            candidates=matching_hits,
        )
    return ClaimDecision(halt_reason=HALT_NO_SEARCH_HIT, create=False)


def decide_settings(
    manuscript: Manuscript,
    mode: CommandMode,
    observation: RemoteObservation,
    claim: ClaimDecision,
) -> SettingsDecision:
    return SettingsDecision()


def decide_chapters(
    manuscript: Manuscript,
    mode: CommandMode,
    observation: RemoteObservation,
    claim: ClaimDecision,
) -> ChaptersDecision:
    return ChaptersDecision()


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
