from __future__ import annotations

import contextlib
import io
import sys
import threading
import uuid
from dataclasses import dataclass, field

from book.jobs import Phase, TaskProgress, buffer_lines
from book.platforms.fanqie import FanqieCrawler
from book.store import Store

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"

_JOBS: dict[str, "CrawlJob"] = {}
_JOBS_LOCK = threading.Lock()
_STDOUT_PROXY_LOCK = threading.Lock()


class CrawlBusy(RuntimeError):
    """已有同步在跑。"""


class _ThreadStdout:
    def __init__(self, original) -> None:
        self._original = original
        self._buffers = threading.local()

    def write(self, data: str) -> int:
        return self._target().write(data)

    def flush(self) -> None:
        flush = getattr(self._target(), "flush", None)
        if flush is not None:
            flush()

    def _target(self):
        buffer = getattr(self._buffers, "current", None)
        return buffer if buffer is not None else self._original

    def __getattr__(self, name: str):
        return getattr(self._original, name)


@contextlib.contextmanager
def capture_stdout():
    buffer = io.StringIO()
    with _STDOUT_PROXY_LOCK:
        current = sys.stdout
        if isinstance(current, _ThreadStdout):
            proxy = current
        else:
            proxy = _ThreadStdout(current)
            sys.stdout = proxy
    previous = getattr(proxy._buffers, "current", None)
    proxy._buffers.current = buffer
    try:
        yield buffer
    finally:
        proxy._buffers.current = previous


@dataclass
class CrawlJob:
    job_id: str
    status: str = JOB_QUEUED
    lines: list[str] = field(default_factory=list)
    halted: str = ""
    progress: TaskProgress | None = None
    buffer: io.StringIO | None = None

    def log_lines(self) -> list[str]:
        if self.buffer is not None:
            live = buffer_lines(self.buffer)
            if live:
                return live
        return list(self.lines)


def get_job(job_id: str) -> CrawlJob | None:
    with _JOBS_LOCK:
        return _JOBS.get(job_id)


def running_job() -> CrawlJob | None:
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job.status in {JOB_QUEUED, JOB_RUNNING}:
                return job
    return None


def reset_jobs() -> None:
    with _JOBS_LOCK:
        _JOBS.clear()


def run_fanqie_crawl(store: Store, progress=None) -> str | None:
    crawler = FanqieCrawler(store)
    try:
        return crawler.crawl(progress=progress)
    finally:
        crawler.close()


def start_crawl_job(store: Store, runner=None) -> CrawlJob:
    if runner is None:
        runner = run_fanqie_crawl
    job = CrawlJob(
        job_id=uuid.uuid4().hex[:12],
        progress=TaskProgress(
            phases=[
                Phase(key="catalog", label="取榜单目录与字体"),
                Phase(key="lists", label="采各榜"),
            ]
        ),
    )
    with _JOBS_LOCK:
        for existing in _JOBS.values():
            if existing.status in {JOB_QUEUED, JOB_RUNNING}:
                raise CrawlBusy("正在同步榜单，等它结束再点。")
        _JOBS[job.job_id] = job
    thread = threading.Thread(
        target=_run_job,
        args=(job, store, runner),
        daemon=True,
        name=f"crawl-{job.job_id}",
    )
    thread.start()
    return job


def _run_job(job: CrawlJob, store: Store, runner) -> None:
    job.status = JOB_RUNNING
    job.lines = ["正在请求番茄公开榜单。"]
    captured = None
    try:
        with capture_stdout() as captured:
            job.buffer = captured
            halted = runner(store, progress=job.progress)
        job.lines = buffer_lines(captured)
        job.buffer = None
        job.halted = halted or ""
        job.status = JOB_DONE
        if not job.lines:
            job.lines = ["同步结束。"]
    except Exception as error:
        extra = buffer_lines(captured)
        job.buffer = None
        job.lines = extra + [f"同步失败：{error}"]
        job.halted = str(error)
        job.status = JOB_FAILED
