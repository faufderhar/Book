from __future__ import annotations

from datetime import date
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from book.board import (
    build_day_board,
    entry_movement,
    format_metric,
    format_sync_status,
    list_title,
)
from book.store import Store
from book.sync import (
    JOB_DONE,
    JOB_FAILED,
    CrawlBusy,
    get_job,
    running_job,
    start_crawl_job,
)

WEB_DIR = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(WEB_DIR / "templates"))
templates.env.filters["metric"] = format_metric
templates.env.filters["list_title"] = list_title
LOCAL_ORIGINS = {"127.0.0.1", "localhost", "::1"}


def reject_foreign_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    host = urlparse(origin).hostname
    if host not in LOCAL_ORIGINS:
        raise HTTPException(status_code=403, detail="只接受本机请求")


def create_app(store: Store | None = None) -> FastAPI:
    app = FastAPI(title="网文风向标")
    app.state.store = store
    app.state.crawl_runner = None
    app.mount("/static", StaticFiles(directory=str(WEB_DIR / "static")), name="static")

    def get_store() -> Store:
        if app.state.store is None:
            app.state.store = Store()
        return app.state.store

    @app.get("/", response_class=HTMLResponse)
    def summary(request: Request, day: str | None = Query(default=None)) -> HTMLResponse:
        current_store = get_store()
        available = current_store.snapshot_dates()
        if day:
            try:
                snapshot_date = date.fromisoformat(day)
            except ValueError as error:
                raise HTTPException(status_code=400, detail="日期格式无效") from error
        elif available:
            snapshot_date = available[0]
        else:
            snapshot_date = date.today()
        board = build_day_board(current_store, snapshot_date)
        latest = current_store.latest_ok_sync()
        return templates.TemplateResponse(
            request,
            "summary.html",
            {
                "board": board,
                "available_dates": available,
                "current_day": snapshot_date,
                "sync_status": format_sync_status(*latest) if latest else "",
                "running_job": running_job(),
            },
        )

    @app.post("/sync")
    def start_sync(request: Request) -> RedirectResponse:
        reject_foreign_origin(request)
        try:
            job = start_crawl_job(get_store(), runner=app.state.crawl_runner)
        except CrawlBusy as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return RedirectResponse(url=f"/sync/{job.job_id}", status_code=303)

    @app.get("/sync/{job_id}", response_class=HTMLResponse)
    def sync_job(request: Request, job_id: str) -> HTMLResponse:
        job = get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="没有这次同步")
        finished = job.status in {JOB_DONE, JOB_FAILED}
        return templates.TemplateResponse(
            request,
            "sync.html",
            {"job": job, "finished": finished},
        )

    @app.get("/list/{platform}/{list_id}", response_class=HTMLResponse)
    def list_detail(
        request: Request, platform: str, list_id: str, day: str = Query(...)
    ) -> HTMLResponse:
        current_store = get_store()
        try:
            snapshot_date = date.fromisoformat(day)
        except ValueError as error:
            raise HTTPException(status_code=400, detail="日期格式无效") from error
        rank_lists = {
            (item.platform, item.list_id): item for item in current_store.list_rank_lists()
        }
        rank_list = rank_lists.get((platform, list_id))
        if rank_list is None:
            raise HTTPException(status_code=404, detail="没有这张榜单")
        snapshot = current_store.get_snapshot(platform, list_id, snapshot_date)
        marks = entry_movement(current_store, snapshot) if snapshot else {}
        previous = (
            current_store.previous_ok_snapshot(platform, list_id, snapshot_date)
            if snapshot
            else None
        )
        left_entries = []
        if previous and snapshot:
            today_ids = snapshot.work_ids
            left_entries = [entry for entry in previous.entries if entry.work_id not in today_ids]
        return templates.TemplateResponse(
            request,
            "list.html",
            {
                "rank_list": rank_list,
                "snapshot": snapshot,
                "snapshot_date": snapshot_date,
                "marks": marks,
                "left_entries": left_entries,
                "has_yesterday": previous is not None,
            },
        )

    return app
