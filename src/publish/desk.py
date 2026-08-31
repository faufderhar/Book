from __future__ import annotations

import contextlib
import io
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from publish.manuscript import (
    PROFILE_FILENAME,
    Manuscript,
    ManuscriptError,
    find_cover_name,
    load_manuscript,
    repo_root,
)
from publish.writer import PublishReport, run_publish

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"

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
    busy = running_job()
    if busy is not None:
        raise ManuscriptError(f"已有发稿在跑：{busy.title}。等它结束再点。")
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
        _JOBS[job.job_id] = job
    thread = threading.Thread(
        target=_run_job,
        args=(job, manuscript, runner),
        daemon=True,
        name=f"publish-{job.job_id}",
    )
    thread.start()
    return job


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
    )


def _cover_ready(profile, manuscript_dir: Path) -> bool:
    return profile.cover_file(manuscript_dir) is not None or bool(find_cover_name(manuscript_dir))


def _run_job(job: PublishJob, manuscript: Manuscript, runner) -> None:
    job.status = JOB_RUNNING
    job.lines = ["正在打开作家后台，本机会弹出浏览器。首次请扫码。"]
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
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
