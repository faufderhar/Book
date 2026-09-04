from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from publish.desk import (
    JOB_DONE,
    JOB_FAILED,
    add_desk_manuscript,
    bind_manuscript,
    desk_settings_view,
    get_job,
    list_desk_rows,
    save_desk_publish_settings,
    start_bind_job,
    start_publish_job,
)
from publish.manuscript import ManuscriptError, repo_root

WEB_DIR = Path(__file__).resolve().parent / "web"
LOCAL_ORIGINS = {"127.0.0.1", "localhost", "::1"}


def reject_foreign_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if not origin:
        return
    host = urlparse(origin).hostname
    if host not in LOCAL_ORIGINS:
        raise HTTPException(status_code=403, detail="只接受本机请求")


BOARD_TEMPLATES = Path(__file__).resolve().parent.parent / "book" / "web" / "templates"
templates = Jinja2Templates(
    directory=[
        str(WEB_DIR / "templates"),
        str(BOARD_TEMPLATES),
    ]
)


def attach_publish_desk(app: FastAPI, *, project_root: Path | None = None) -> None:
    app.state.publish_root = project_root or repo_root()

    def current_root() -> Path:
        return getattr(app.state, "publish_root", repo_root())

    @app.get("/publish", response_class=HTMLResponse)
    def publish_desk(request: Request) -> HTMLResponse:
        rows = list_desk_rows(current_root())
        return templates.TemplateResponse(
            request,
            "desk.html",
            {"rows": rows, "error": "", "work_title": ""},
        )

    @app.post("/publish/manuscripts", response_model=None)
    def add_manuscript(
        request: Request,
        work_title: str = Form(""),
    ) -> RedirectResponse | HTMLResponse:
        reject_foreign_origin(request)
        try:
            add_desk_manuscript(work_title, root=current_root())
        except ManuscriptError as error:
            rows = list_desk_rows(current_root())
            return templates.TemplateResponse(
                request,
                "desk.html",
                {"rows": rows, "error": str(error), "work_title": work_title},
                status_code=400,
            )
        return RedirectResponse(url="/publish", status_code=303)

    @app.post("/publish/{directory_name}/jobs")
    def create_publish_job(
        request: Request,
        directory_name: str,
        dry_run: bool = False,
        allow_create: bool = False,
    ) -> RedirectResponse:
        reject_foreign_origin(request)
        try:
            job = start_publish_job(
                directory_name,
                dry_run=dry_run,
                allow_create=allow_create,
                root=current_root(),
            )
        except ManuscriptError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return RedirectResponse(url=f"/publish/jobs/{job.job_id}", status_code=303)

    @app.post("/publish/{directory_name}/bind-jobs")
    def create_bind_job(
        request: Request,
        directory_name: str,
    ) -> RedirectResponse:
        reject_foreign_origin(request)
        try:
            job = start_bind_job(directory_name, root=current_root())
        except ManuscriptError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return RedirectResponse(url=f"/publish/jobs/{job.job_id}", status_code=303)

    @app.post("/publish/{directory_name}/bind")
    def bind_platform_book(
        request: Request,
        directory_name: str,
        book_id: str = Form(...),
    ) -> RedirectResponse:
        reject_foreign_origin(request)
        try:
            bind_manuscript(directory_name, book_id, root=current_root())
        except ManuscriptError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        return RedirectResponse(url="/publish", status_code=303)

    @app.get("/publish/{directory_name}/settings", response_class=HTMLResponse)
    def publish_settings(
        request: Request,
        directory_name: str,
        saved: bool = False,
    ) -> HTMLResponse:
        try:
            context = desk_settings_view(directory_name, root=current_root())
        except ManuscriptError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        context.update(saved=saved, error="")
        return templates.TemplateResponse(request, "settings.html", context)

    @app.post("/publish/{directory_name}/settings", response_model=None)
    def save_publish_settings(
        request: Request,
        directory_name: str,
        chapter_visibility: str = Form(...),
        serial_status: str = Form(...),
        max_chapters_per_run: str = Form(...),
        delay_seconds: str = Form(...),
        human_wait_seconds: str = Form(...),
        schedule_times: str = Form(""),
        book_id: str = Form(""),
        work_title: str = Form(""),
        channel: str = Form(""),
        category: str = Form(""),
        intro: str = Form(""),
        cover: UploadFile | None = File(None),
    ) -> RedirectResponse | HTMLResponse:
        reject_foreign_origin(request)
        cover_filename = ""
        cover_bytes: bytes | None = None
        if cover is not None and cover.filename:
            cover_filename = cover.filename
            cover_bytes = cover.file.read()
        try:
            save_desk_publish_settings(
                directory_name,
                book_id=book_id,
                chapter_visibility=chapter_visibility,
                serial_status=serial_status,
                max_chapters_per_run=max_chapters_per_run,
                delay_seconds=delay_seconds,
                human_wait_seconds=human_wait_seconds,
                schedule_times=schedule_times,
                root=current_root(),
                work_title=work_title,
                channel=channel,
                category=category,
                intro=intro,
                cover_filename=cover_filename,
                cover_bytes=cover_bytes,
            )
        except ManuscriptError as error:
            try:
                context = desk_settings_view(directory_name, root=current_root())
            except ManuscriptError as load_error:
                raise HTTPException(status_code=400, detail=str(load_error)) from load_error
            context.update(
                saved=False,
                error=str(error),
                form={
                    "book_id": book_id,
                    "work_title": work_title,
                    "channel": channel,
                    "category": category,
                    "intro": intro,
                    "cover": context["form"].get("cover", ""),
                    "chapter_visibility": chapter_visibility,
                    "serial_status": serial_status,
                    "max_chapters_per_run": max_chapters_per_run,
                    "delay_seconds": delay_seconds,
                    "human_wait_seconds": human_wait_seconds,
                    "schedule_times": schedule_times,
                },
            )
            return templates.TemplateResponse(
                request,
                "settings.html",
                context,
                status_code=400,
            )
        return RedirectResponse(
            url=f"/publish/{directory_name}/settings?saved=1",
            status_code=303,
        )

    @app.get("/publish/jobs/{job_id}", response_class=HTMLResponse)
    def publish_job(request: Request, job_id: str) -> HTMLResponse:
        job = get_job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="没有这次发稿")
        finished = job.status in {JOB_DONE, JOB_FAILED}
        return templates.TemplateResponse(
            request,
            "job.html",
            {"job": job, "finished": finished},
        )
