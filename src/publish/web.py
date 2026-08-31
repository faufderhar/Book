from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from publish.desk import (
    JOB_DONE,
    JOB_FAILED,
    get_job,
    list_desk_rows,
    start_publish_job,
)
from publish.manuscript import ManuscriptError, repo_root

WEB_DIR = Path(__file__).resolve().parent / "web"
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
            {"rows": rows},
        )

    @app.post("/publish/{directory_name}/jobs")
    def create_publish_job(
        directory_name: str,
        dry_run: bool = False,
        allow_create: bool = False,
    ) -> RedirectResponse:
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
