"""API 只提交和查询持久化任务，不在请求线程执行长任务。"""

from typing import Literal
from uuid import UUID

from fastapi import Depends, Request, Response
from pydantic import Field

from . import job_service
from .job_config import load_worker_configuration
from .schemas import StrictRequest


class ModelCheckRequest(StrictRequest):
    kind: Literal["model_integrity_check"]
    model_version: str | None = Field(default=None, min_length=1, max_length=64)
    idempotency_key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


def register_job_routes(app, get_session, current, envelope):
    config = load_worker_configuration()

    @app.post("/api/jobs", status_code=202)
    def submit(request: Request, response: Response, payload: ModelCheckRequest,
               principal=Depends(current), session=Depends(get_session)):
        result, replayed = job_service.submit_model_check(session, principal, payload,
            request.app.state.device_runtime, config, request.state.request_id)
        session.commit()
        response.status_code = 200 if replayed else 202
        return envelope(request, {**result, "replayed": replayed})

    @app.get("/api/jobs")
    def listing(request: Request, offset: int = 0, limit: int = 20, deleted: bool = False,
                principal=Depends(current), session=Depends(get_session)):
        return envelope(request, job_service.list_jobs(session, principal, offset, limit, deleted))

    @app.get("/api/jobs/{job_id}")
    def detail(request: Request, job_id: UUID, principal=Depends(current), session=Depends(get_session)):
        return envelope(request, job_service.job_view(session, job_service.owned_job(session, principal, job_id)))

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel(request: Request, job_id: UUID, principal=Depends(current), session=Depends(get_session)):
        result = job_service.cancel_job(session, principal, job_id, request.state.request_id)
        session.commit()
        return envelope(request, result)
