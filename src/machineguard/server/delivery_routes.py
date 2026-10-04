"""报告内次级交付入口，所有下载都重新鉴权并校验私有对象。"""
from typing import Literal
from uuid import UUID
from fastapi import Depends, Request, Response
from pydantic import Field, field_validator
from sqlalchemy import select
from .schemas import StrictRequest
from .index_routes import PublishRequest
from . import delivery_service as service, report_service
from .delivery_models import ReportExport, MaintenanceTask
from .oss_storage import OssStorage
from .report_render import MIME
from .auth_service import ServiceError


class ExportRequest(StrictRequest):
    format: Literal["md", "json", "pdf"]
    idempotency_key: UUID

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def key(cls, value):
        return PublishRequest.json_uuid(value)


class MaintenanceCreate(StrictRequest):
    idempotency_key: UUID
    title: str = Field(min_length=1, max_length=120)
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def key(cls, value):
        return PublishRequest.json_uuid(value)

    @field_validator("title", "note")
    @classmethod
    def meaningful(cls, value):
        if not value.strip() or any(ord(c) < 32 and c not in "\n\r\t" for c in value):
            raise ValueError("任务说明不能为空或包含控制字符。")
        return value.strip()


class MaintenanceUpdate(StrictRequest):
    idempotency_key: UUID
    expected_revision: int = Field(ge=1)
    status: Literal["in_progress", "completed", "cancelled"]
    note: str = Field(min_length=1, max_length=2000)
    key = field_validator("idempotency_key", mode="before")(MaintenanceCreate.key.__func__)
    meaningful = field_validator("note")(MaintenanceCreate.meaningful.__func__)


def register_delivery_routes(app, get_session, current, envelope):
    @app.post("/api/reports/{report_id}/exports")
    def create_export(request: Request, report_id: UUID, payload: ExportRequest, principal=Depends(current), session=Depends(get_session)):
        result = service.submit_export(session, principal, report_id, payload, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.get("/api/reports/{report_id}/exports")
    def exports(request: Request, response: Response, report_id: UUID, principal=Depends(current), session=Depends(get_session)):
        report_service.authorized_report(session, principal, report_id)
        rows = session.scalars(select(ReportExport).where(ReportExport.report_id == report_id, ReportExport.creator_id == principal.user.id)
                               .order_by(ReportExport.created_at.desc()).limit(20)).all()
        response.headers["Cache-Control"] = "no-store"
        return envelope(request, {"items": [service.export_view(session, row) for row in rows if session.get(service.Job, row.job_id).deleted_at is None]})

    @app.get("/api/reports/{report_id}/export")
    def download(request: Request, report_id: UUID, export_id: UUID, principal=Depends(current), session=Depends(get_session)):
        row = service.owned_export(session, principal, report_id, export_id)
        if not service.export_view(session, row)["can_download"]:
            raise ServiceError(409, "EXPORT_NOT_READY", "导出尚未成功保存，不能下载。")
        data = OssStorage().read_verified(row.artifact["key"], row.artifact["size"], row.artifact["sha256"])
        return Response(content=data, media_type=MIME[row.format], headers={"Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="MachineGuard-{report_id}.{row.format}"',
            "X-Export-SHA256": row.artifact["sha256"]})

    @app.get("/api/reports/{report_id}/maintenance")
    def tasks(request: Request, report_id: UUID, principal=Depends(current), session=Depends(get_session)):
        report_service.authorized_report(session, principal, report_id)
        row = session.scalar(select(MaintenanceTask).where(MaintenanceTask.report_id == report_id))
        return envelope(request, {"items": [service.task_view(session, row)] if row else []})

    @app.post("/api/reports/{report_id}/maintenance")
    def create_task(request: Request, report_id: UUID, payload: MaintenanceCreate, principal=Depends(current), session=Depends(get_session)):
        result = service.create_task(session, principal, report_id, payload, request.state.request_id)
        session.commit(); return envelope(request, result)

    @app.patch("/api/maintenance-tasks/{task_id}")
    def update_task(request: Request, task_id: UUID, payload: MaintenanceUpdate, principal=Depends(current), session=Depends(get_session)):
        result = service.update_task(session, principal, task_id, payload, request.state.request_id)
        session.commit(); return envelope(request, result)
