from __future__ import annotations

import contextlib
import io
import threading
import uuid
from dataclasses import dataclass, field

from book.platforms.fanqie import FanqieCrawler
from book.store import Store

JOB_QUEUED = "queued"
JOB_RUNNING = "running"
JOB_DONE = "done"
JOB_FAILED = "failed"

_JOBS: dict[str, "CrawlJob"] = {}
_JOBS_LOCK = threading.Lock()


class CrawlBusy(RuntimeError):
    """已有同步在跑。"""


@dataclass
class CrawlJob:
    job_id: str
    status: str = JOB_QUEUED
    lines: list[str] = field(default_factory=list)
    halted: str = ""


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


def run_fanqie_crawl(store: Store) -> str | None:
    crawler = FanqieCrawler(store)
    try:
        return crawler.crawl()
    finally:
        crawler.close()


def start_crawl_job(store: Store, runner=None) -> CrawlJob:
    if runner is None:
        runner = run_fanqie_crawl
    job = CrawlJob(job_id=uuid.uuid4().hex[:12])
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
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            halted = runner(store)
        job.lines = [line for line in buffer.getvalue().splitlines() if line]
        job.halted = halted or ""
        job.status = JOB_DONE
        if not job.lines:
            job.lines = ["同步结束。"]
    except Exception as error:
        extra = [line for line in buffer.getvalue().splitlines() if line]
        job.lines = extra + [f"同步失败：{error}"]
        job.halted = str(error)
        job.status = JOB_FAILED
