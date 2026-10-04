"""报告授权、导出提交与模拟维护的幂等状态转换。"""
from datetime import timedelta
from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID
from sqlalchemy import func, select, text
from .auth_service import Principal, ServiceError, audit
from .delivery_models import ReportExport, MaintenanceTask, MaintenanceAction
from .job_models import Job
from .models import User
from . import job_service, report_service, agent_service
from .job_config import load_worker_configuration
from ..paths import get_project_root


def locked_key(session, value):
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"),
                    {"key": int.from_bytes(sha256(value.encode()).digest()[:8], "big", signed=True)})


def render_configuration(format):
    try:
        config = json.loads((get_project_root() / "configs/export.json").read_text(encoding="utf-8"))
        if set(config) != {"renderer_version", "reportlab_version", "pdf_font_path"} or config["renderer_version"] != "report-delivery-v1":
            raise ValueError()
        if format == "pdf":
            import reportlab
            if reportlab.Version != config["reportlab_version"]:
                raise ValueError()
            path = Path(config["pdf_font_path"])
            if not path.is_absolute() or not path.is_file():
                raise ValueError()
            config["font_sha256"] = sha256(path.read_bytes()).hexdigest()
        return config
    except (OSError, ValueError, TypeError, KeyError, ImportError):
        raise ServiceError(503, "EXPORT_CONFIG_INVALID", "导出配置、固定版本或中文字体不可用，请核对 configs/export.json。") from None


def export_view(session, row):
    job = session.get(Job, row.job_id)
    return {"export_id": str(row.id), "report_id": str(row.report_id), "format": row.format,
            "created_at": row.created_at, "job": job_service.job_view(session, job),
            "sha256": row.artifact["sha256"] if row.artifact else None,
            "size": row.artifact["size"] if row.artifact else None,
            "can_download": row.artifact is not None and job.status == "succeeded" and job.deleted_at is None}


def submit_export(session, principal, report_id, payload, request_id):
    report = report_service.authorized_report(session, principal, report_id)
    locked_key(session, f"jobs:{principal.user.id}")
    target = {"report_id": str(report_id), "format": payload.format}
    fingerprint = sha256(json.dumps(target, sort_keys=True).encode()).hexdigest()
    existing = session.scalar(select(Job).where(Job.owner_id == principal.user.id, Job.idempotency_key == str(payload.idempotency_key)))
    if existing:
        if existing.kind != "report_export" or existing.request_fingerprint != fingerprint:
            raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "该请求键已用于不同导出参数。")
        row = session.scalar(select(ReportExport).where(ReportExport.job_id == existing.id))
        return {**export_view(session, row), "replayed": True}
    cfg = load_worker_configuration()
    pending = session.scalar(select(func.count()).select_from(Job).where(Job.owner_id == principal.user.id, Job.status.in_(["queued", "running"])))
    if pending >= cfg.max_pending_per_owner:
        raise ServiceError(429, "JOB_LIMIT_REACHED", "待处理任务数量已达到上限。")
    config = render_configuration(payload.format)
    now = job_service.db_now(session)
    job = Job(owner_id=principal.user.id, kind="report_export", payload=target, idempotency_key=str(payload.idempotency_key),
              request_fingerprint=fingerprint, request_id=request_id, created_at=now,
              deadline_at=now + timedelta(seconds=cfg.timeout_seconds), max_attempts=cfg.max_attempts)
    session.add(job); session.flush()
    row = ReportExport(report_id=report.id, creator_id=principal.user.id, job_id=job.id, format=payload.format,
                       content_sha256=report.content_sha256, render_config=config)
    session.add(row); session.flush()
    job.payload = {**target, "export_id": str(row.id)}
    audit(session, principal.user.id, "report.export_submitted", "report_export", row.id, request_id, target)
    return {**export_view(session, row), "replayed": False}


def owned_export(session, principal, report_id, export_id):
    report_service.authorized_report(session, principal, report_id)
    row = session.scalar(select(ReportExport).where(ReportExport.id == export_id, ReportExport.report_id == report_id,
                                                  ReportExport.creator_id == principal.user.id))
    if row is None or session.get(Job, row.job_id).deleted_at is not None:
        raise ServiceError(404, "EXPORT_NOT_FOUND", "导出不存在或不属于当前身份。")
    return row


def execution_export(session, export_id):
    row = session.get(ReportExport, export_id)
    if row is None:
        raise ServiceError(404, "EXPORT_NOT_FOUND", "导出记录不存在。")
    user = session.get(User, row.creator_id)
    if user is None or not user.is_active:
        raise ServiceError(403, "EXPORT_OWNER_FORBIDDEN", "导出账号已停用。")
    report = report_service.authorized_report(session, Principal(user, None), row.report_id)
    if report.content_sha256 != row.content_sha256 or report_service.digest(report.content) != row.content_sha256:
        raise ServiceError(409, "EXPORT_REPORT_CHANGED", "固定报告内容校验失败。")
    return row, report


def finish_export(session, lease, result):
    job, _ = job_service._live(session, lease)
    if job is None:
        return False
    row, _ = execution_export(session, UUID(lease.payload["export_id"]))
    if row.job_id != job.id or result["format"] != row.format:
        raise ServiceError(409, "EXPORT_RESULT_INVALID", "导出执行结果不匹配。")
    row.artifact = result
    audit(session, row.creator_id, "report.export_saved", "report_export", row.id, job.request_id,
          {"sha256": result["sha256"], "size": result["size"], "format": row.format})
    return job_service.finish_job(session, lease, result={key: result[key] for key in ("sha256", "size", "format")})


def task_view(session, row):
    actions = session.scalars(select(MaintenanceAction).where(MaintenanceAction.task_id == row.id).order_by(MaintenanceAction.revision)).all()
    return {"task_id": str(row.id), "report_id": str(row.report_id), "title": row.title, "note": row.note,
            "status": row.status, "revision": row.revision, "created_at": row.created_at, "updated_at": row.updated_at,
            "simulation": True, "actions": [{"revision": action.revision, "status": action.status, "note": action.note,
               "actor_name": session.get(User, action.actor_id).display_name, "created_at": action.created_at} for action in actions]}


def create_task(session, principal, report_id, payload, request_id):
    report_service.authorized_report(session, principal, report_id); principal.require("reports:review")
    locked_key(session, f"maintenance:{report_id}")
    row = session.scalar(select(MaintenanceTask).where(MaintenanceTask.report_id == report_id))
    if row:
        if (row.creator_id, row.creation_key, row.title, row.note) != (principal.user.id, payload.idempotency_key, payload.title, payload.note):
            raise ServiceError(409, "MAINTENANCE_EXISTS", "此报告已有模拟维护任务，请读取并更新原任务。")
        return {**task_view(session, row), "replayed": True}
    row = MaintenanceTask(report_id=report_id, creator_id=principal.user.id, creation_key=payload.idempotency_key,
                          title=payload.title, note=payload.note)
    session.add(row); session.flush()
    session.add(MaintenanceAction(task_id=row.id, actor_id=principal.user.id, idempotency_key=payload.idempotency_key,
        expected_revision=0, revision=1, status="pending", note=payload.note)); session.flush()
    audit(session, principal.user.id, "maintenance.created", "maintenance_task", row.id, request_id, {"simulation": True, "report_id": str(report_id)})
    return {**task_view(session, row), "replayed": False}


def update_task(session, principal, task_id, payload, request_id):
    principal.require("reports:review")
    # 先锁任务，再读取版本和请求键，保护并发更新与不可变历史。
    row = session.scalar(select(MaintenanceTask).where(MaintenanceTask.id == task_id).with_for_update())
    if row is None:
        raise ServiceError(404, "MAINTENANCE_NOT_FOUND", "模拟任务不存在。")
    report_service.authorized_report(session, principal, row.report_id)
    existing = session.scalar(select(MaintenanceAction).where(MaintenanceAction.task_id == row.id, MaintenanceAction.idempotency_key == payload.idempotency_key))
    if existing:
        if (existing.actor_id, existing.expected_revision, existing.status, existing.note) != (principal.user.id, payload.expected_revision, payload.status, payload.note):
            raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "该请求键已用于不同任务更新。")
        return {**task_view(session, row), "replayed": True}
    if row.revision != payload.expected_revision:
        raise ServiceError(409, "MAINTENANCE_STALE", "任务已被更新，请刷新后核对。")
    allowed = {"pending": {"in_progress", "cancelled"}, "in_progress": {"completed", "cancelled"}, "completed": set(), "cancelled": set()}
    if payload.status not in allowed[row.status]:
        raise ServiceError(409, "MAINTENANCE_TRANSITION_INVALID", "模拟任务不允许此状态转换。")
    row.status = payload.status; row.revision += 1; row.updated_at = job_service.db_now(session)
    session.add(MaintenanceAction(task_id=row.id, actor_id=principal.user.id, idempotency_key=payload.idempotency_key,
        expected_revision=payload.expected_revision, revision=row.revision, status=row.status, note=payload.note)); session.flush()
    audit(session, principal.user.id, "maintenance.updated", "maintenance_task", row.id, request_id,
          {"simulation": True, "status": row.status, "revision": row.revision})
    return {**task_view(session, row), "replayed": False}
