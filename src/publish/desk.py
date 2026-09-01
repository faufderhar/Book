from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from book.sync import capture_stdout
from publish.manuscript import (
    PROFILE_FILENAME,
    BookProfile,
    Manuscript,
    ManuscriptError,
    SERIAL_CHOICES,
    VISIBILITY_CHOICES,
    apply_publish_fields,
    find_cover_name,
    load_manuscript,
    load_profile,
    preview_publish_slots,
    repo_root,
    save_profile,
)
from publish.plan import SearchHit
from publish.writer import PublishReport, run_list_platform_books, run_publish

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"
KIND_PUBLISH = "发稿"
KIND_BIND = "绑定"

_JOBS: dict[str, "PublishJob"] = {}
_JOBS_LOCK = threading.Lock()


@dataclass
class DeskRow:
    directory_name: str
    title: str
    book_id: str
    chapter_count: int
    cover_ready: bool
    bound: bool
    load_error: str = ""
    chapter_visibility: str = ""
    max_chapters_per_run: int = 20
    serial_status: str = ""
    schedule_times: tuple[str, ...] = ()


@dataclass
class PublishJob:
    job_id: str
    directory_name: str
    title: str
    dry_run: bool
    allow_create: bool
    status: str = JOB_QUEUED
    lines: list[str] = field(default_factory=list)
    halted: str = ""
    claimed_book_id: str = ""
    kind: str = KIND_PUBLISH
    candidates: tuple[SearchHit, ...] = ()
    bound_book_id: str = ""


def novel_root(root: Path | None = None) -> Path:
    return (root or repo_root()) / "novel"


def list_desk_rows(root: Path | None = None) -> list[DeskRow]:
    base = novel_root(root)
    if not base.is_dir():
        return []
    rows: list[DeskRow] = []
    for path in sorted(base.iterdir()):
        if not path.is_dir():
            continue
        if not (path / PROFILE_FILENAME).is_file():
            continue
        rows.append(_desk_row_for(path))
    return rows


def resolve_manuscript_dir(directory_name: str, root: Path | None = None) -> Path:
    if not directory_name or "/" in directory_name or "\\" in directory_name:
        raise ManuscriptError("稿本目录名不合法")
    if directory_name in {".", ".."}:
        raise ManuscriptError("稿本目录名不合法")
    base = novel_root(root).resolve()
    path = (base / directory_name).resolve()
    if path.parent != base or not path.is_dir():
        raise ManuscriptError(f"没有这份稿本：{directory_name}")
    return path


def get_job(job_id: str) -> PublishJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def running_job() -> PublishJob | None:
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.status in {JOB_QUEUED, JOB_RUNNING}:
                return job
    return None


def reset_jobs() -> None:
    with _JOBS_LOCK:
        _JOBS.clear()


def start_publish_job(
    directory_name: str,
    *,
    dry_run: bool = False,
    allow_create: bool = False,
    root: Path | None = None,
    runner=None,
) -> PublishJob:
    if runner is None:
        runner = run_publish
    manuscript_dir = resolve_manuscript_dir(directory_name, root=root)
    manuscript = load_manuscript(manuscript_dir)
    job = PublishJob(
        job_id=uuid.uuid4().hex[:12],
        directory_name=directory_name,
        title=manuscript.profile.field_text("作品名称") or directory_name,
        dry_run=dry_run,
        allow_create=allow_create,
    )
    with _JOBS_LOCK:
        _reject_if_busy()
        _JOBS[job.job_id] = job
    thread = threading.Thread(
        target=_run_job,
        args=(job, manuscript, runner),
        daemon=True,
        name=f"publish-{job.job_id}",
    )
    thread.start()
    return job


def start_bind_job(
    directory_name: str,
    *,
    root: Path | None = None,
    runner=None,
) -> PublishJob:
    if runner is None:
        runner = run_list_platform_books
    profile = load_desk_profile(directory_name, root=root)
    job = PublishJob(
        job_id=uuid.uuid4().hex[:12],
        directory_name=directory_name,
        title=profile.field_text("作品名称") or directory_name,
        dry_run=False,
        allow_create=False,
        kind=KIND_BIND,
        bound_book_id=profile.book_id,
    )
    with _JOBS_LOCK:
        _reject_if_busy()
        _JOBS[job.job_id] = job
    thread = threading.Thread(
        target=_run_bind_job,
        args=(job, profile, runner),
        daemon=True,
        name=f"bind-{job.job_id}",
    )
    thread.start()
    return job


def _reject_if_busy() -> None:
    for existing in _JOBS.values():
        if existing.status in {JOB_QUEUED, JOB_RUNNING}:
            raise ManuscriptError(f"已有任务在跑：{existing.title}。等它结束再点。")


def bind_manuscript(
    directory_name: str,
    book_id: str,
    *,
    root: Path | None = None,
) -> BookProfile:
    busy = running_job()
    if busy is not None and busy.directory_name == directory_name:
        raise ManuscriptError(f"正在发稿：{busy.title}。结束后再改绑定。")
    normalized = str(book_id or "").strip()
    if not normalized:
        raise ManuscriptError("没有作品 ID")
    _reject_if_book_taken(normalized, directory_name, root=root)
    profile = load_desk_profile(directory_name, root=root)
    profile.rebind(normalized)
    save_profile(profile)
    return profile


def _reject_if_book_taken(book_id: str, directory_name: str, root: Path | None = None) -> None:
    if not book_id:
        return
    owner = _bound_owner(book_id, directory_name, root=root)
    if owner is not None:
        raise ManuscriptError(f"平台作品 {book_id} 已绑定稿本「{owner}」")


def _bound_owner(book_id: str, directory_name: str, root: Path | None = None) -> str | None:
    for row in list_desk_rows(root):
        if row.directory_name == directory_name:
            continue
        if row.book_id == book_id:
            return row.title or row.directory_name
    return None


def _desk_row_for(path: Path) -> DeskRow:
    directory_name = path.name
    try:
        manuscript = load_manuscript(path)
    except ManuscriptError as error:
        return DeskRow(
            directory_name=directory_name,
            title=directory_name,
            book_id="",
            chapter_count=0,
            cover_ready=False,
            bound=False,
            load_error=str(error),
        )
    profile = manuscript.profile
    return DeskRow(
        directory_name=directory_name,
        title=profile.field_text("作品名称") or directory_name,
        book_id=profile.book_id,
        chapter_count=len(manuscript.chapters),
        cover_ready=_cover_ready(profile, path),
        bound=bool(profile.book_id),
        chapter_visibility=profile.chapter_visibility,
        max_chapters_per_run=profile.max_chapters_per_run,
        serial_status=profile.serial_status,
        schedule_times=profile.schedule_times,
    )


def _cover_ready(profile, manuscript_dir: Path) -> bool:
    return profile.cover_file(manuscript_dir) is not None or bool(find_cover_name(manuscript_dir))


def _run_job(job: PublishJob, manuscript: Manuscript, runner) -> None:
    job.status = JOB_RUNNING
    job.lines = ["正在打开作家后台，本机会弹出浏览器。首次请扫码。"]
    try:
        with capture_stdout() as buffer:
            report: PublishReport = runner(
                manuscript,
                dry_run=job.dry_run,
                discover_only=False,
                allow_create=job.allow_create,
            )
        job.lines = [line for line in buffer.getvalue().splitlines() if line]
        job.halted = report.halted or ""
        job.claimed_book_id = report.claimed_book_id
        job.status = JOB_DONE
        if not job.lines:
            job.lines = ["发稿结束，没有对照结果。"]
    except Exception as error:
        extra = [line for line in buffer.getvalue().splitlines() if line]
        job.lines = extra + [f"发稿失败：{error}"]
        job.halted = str(error)
        job.status = JOB_FAILED


def _run_bind_job(job: PublishJob, profile: BookProfile, runner) -> None:
    job.status = JOB_RUNNING
    job.lines = ["正在打开作家后台，本机会弹出浏览器。首次请扫码。"]
    try:
        with capture_stdout() as buffer:
            hits = runner(profile)
        job.candidates = tuple(hits)
        job.lines = [line for line in buffer.getvalue().splitlines() if line]
        if not job.lines:
            job.lines = [f"作品管理 {len(job.candidates)} 本"]
        job.status = JOB_DONE
    except Exception as error:
        extra = [line for line in buffer.getvalue().splitlines() if line]
        job.lines = extra + [f"绑定失败：{error}"]
        job.halted = str(error)
        job.status = JOB_FAILED


def load_desk_profile(directory_name: str, root: Path | None = None) -> BookProfile:
    manuscript_dir = resolve_manuscript_dir(directory_name, root=root)
    profile_path = manuscript_dir / PROFILE_FILENAME
    if not profile_path.is_file():
        raise ManuscriptError(f"缺少 {PROFILE_FILENAME}")
    return load_profile(profile_path)


def settings_form_from_profile(profile: BookProfile) -> dict[str, str]:
    return {
        "book_id": profile.book_id,
        "chapter_visibility": profile.chapter_visibility,
        "serial_status": profile.serial_status,
        "max_chapters_per_run": str(profile.max_chapters_per_run),
        "delay_seconds": _format_quantity(profile.delay_seconds),
        "human_wait_seconds": _format_quantity(profile.human_wait_seconds),
        "schedule_times": "、".join(profile.schedule_times),
    }


def desk_settings_view(directory_name: str, root: Path | None = None) -> dict:
    profile = load_desk_profile(directory_name, root=root)
    busy = running_job()
    return {
        "directory_name": directory_name,
        "title": profile.field_text("作品名称") or directory_name,
        "form": settings_form_from_profile(profile),
        "upcoming": preview_publish_slots(profile),
        "locked": busy is not None and busy.directory_name == directory_name,
        "visibility_choices": VISIBILITY_CHOICES,
        "serial_choices": SERIAL_CHOICES,
    }


def save_desk_publish_settings(
    directory_name: str,
    *,
    book_id: str,
    chapter_visibility: str,
    serial_status: str,
    max_chapters_per_run: str,
    delay_seconds: str,
    human_wait_seconds: str,
    schedule_times: str,
    root: Path | None = None,
) -> BookProfile:
    busy = running_job()
    if busy is not None and busy.directory_name == directory_name:
        raise ManuscriptError(f"正在发稿：{busy.title}。结束后再改设置。")
    profile = load_desk_profile(directory_name, root=root)
    normalized = str(book_id or "").strip()
    _reject_if_book_taken(normalized, directory_name, root=root)
    profile.rebind(normalized)
    apply_publish_fields(
        profile,
        {
            "章节可见性": chapter_visibility,
            "连载状态": serial_status,
            "单次章数上限": max_chapters_per_run,
            "章间隔秒": delay_seconds,
            "人工等待秒": human_wait_seconds,
            "发稿时刻": schedule_times,
        },
    )
    save_profile(profile)
    return profile


def _format_quantity(value: float) -> str:
    if value == int(value):
        return str(int(value))
    return str(value)
