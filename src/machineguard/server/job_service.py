"""共用任务服务：授权、幂等提交、原子领取、心跳与租约到期处理。"""

from dataclasses import dataclass
from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID, uuid4

from sqlalchemy import func, select, text

from .auth_service import ServiceError, audit
from .device_service import pagination
from .job_models import Job, JobAttempt
from .models import User
from .security import permissions


TERMINAL = {"succeeded", "failed", "cancelled"}


@dataclass(frozen=True)
class Lease:
    job_id: UUID
    token: UUID
    kind: str
    payload: dict
    number: int


class JobInterrupted(Exception):
    """阶段边界检测到取消、时限或失去租约，不能继续写入。"""


def db_now(session):
    # 使用数据库实际时钟，不使用事务开始时间或 Worker 本机时间。
    return session.scalar(select(func.clock_timestamp()))


def job_view(session, row):
    attempts = session.scalars(select(JobAttempt).where(JobAttempt.job_id == row.id)
                              .order_by(JobAttempt.number)).all()
    from .qa_models import KnowledgeAnswer, KnowledgeConversation
    answer = session.scalar(select(KnowledgeAnswer).where(KnowledgeAnswer.job_id == row.id)) if row.kind == "knowledge_answer" else None
    conversation = session.get(KnowledgeConversation, answer.conversation_id) if answer is not None and answer.conversation_id else None
    return {"deleted_at": row.deleted_at, "answer_available": conversation is None or conversation.deleted_at is None, "job_id": str(row.id), "kind": row.kind, "status": row.status, "stage": row.stage,
            "progress": row.progress, "payload": row.payload, "request_id": str(row.request_id),
            "attempt_count": row.attempt_count, "max_attempts": row.max_attempts,
            "created_at": row.created_at.isoformat(), "started_at": row.started_at,
            "finished_at": row.finished_at, "deadline_at": row.deadline_at,
            "heartbeat_at": row.heartbeat_at, "lease_expires_at": row.lease_expires_at,
            "cancel_requested_at": row.cancel_requested_at, "result": row.result,
            "error": {"code": row.error_code, "message": row.error_message} if row.error_code else None,
            "attempts": [{"number": a.number, "worker_id": a.worker_id, "status": a.status,
                          "stage": a.stage, "started_at": a.started_at, "heartbeat_at": a.heartbeat_at,
                          "finished_at": a.finished_at, "error_code": a.error_code,
                          "error_message": a.error_message} for a in attempts]}


def owned_job(session, principal, job_id, lock=False, include_deleted=False):
    query = select(Job).where(Job.id == job_id, Job.owner_id == principal.user.id)
    if not include_deleted:
        query = query.where(Job.deleted_at.is_(None))
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        raise ServiceError(404, "JOB_NOT_FOUND", "任务不存在或不属于当前身份。")
    return row


def submit_model_check(session, principal, request, runtime, config, request_id):
    principal.require("system:read")
    # 同一用户提交串行化，同时保护请求键与待处理任务数量上限。
    lock_id = int.from_bytes(sha256(f"jobs:{principal.user.id}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_id})
    existing = session.scalar(select(Job).where(Job.owner_id == principal.user.id,
                                               Job.idempotency_key == request.idempotency_key))
    if existing is not None:
        # 模型后来变更也能确认原任务；显式不同的模型请求不能复用原键。
        if existing.kind != request.kind or (request.model_version is not None and
                                             request.model_version != existing.payload["model_version"]):
            raise ServiceError(409, "IDEMPOTENCY_CONFLICT", "该请求键已用于不同任务参数。")
        return job_view(session, existing), True
    registered = runtime.check_registration(session)
    version = request.model_version if request.model_version is not None else registered.version
    if version != registered.version:
        raise ServiceError(422, "MODEL_VERSION_INVALID", "只能校验当前已加载且已发布的模型。")
    payload = {"model_version": version, "manifest_sha256": registered.manifest_sha256,
               "weight_sha256": registered.weight_sha256}
    fingerprint = sha256(json.dumps({"kind": request.kind, "payload": payload}, sort_keys=True,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()
    pending = session.scalar(select(func.count()).select_from(Job).where(
        Job.owner_id == principal.user.id, Job.status.in_(["queued", "running"])))
    if pending >= config.max_pending_per_owner:
        raise ServiceError(429, "JOB_LIMIT_REACHED", "待处理任务数量已达到上限，请等待或取消已有任务。")
    now = db_now(session)
    row = Job(owner_id=principal.user.id, kind=request.kind, payload=payload,
              idempotency_key=request.idempotency_key, request_fingerprint=fingerprint,
              request_id=request_id, created_at=now, deadline_at=now + timedelta(seconds=config.timeout_seconds),
              max_attempts=config.max_attempts)
    session.add(row)
    session.flush()
    audit(session, principal.user.id, "job.submitted", "job", row.id, request_id,
          {"kind": row.kind, "model_version": version})
    return job_view(session, row), False


def list_jobs(session, principal, offset=0, limit=20, deleted=False):
    pagination(offset, limit)
    query = select(Job).where(Job.owner_id == principal.user.id)
    query = query.where(Job.deleted_at.is_not(None) if deleted else Job.deleted_at.is_(None))
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(Job.created_at.desc(), Job.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [job_view(session, row) for row in rows], "total": total, "offset": offset, "limit": limit}


def _close(session, row, status, now, code=None, message=None, result=None):
    if row.lease_token is not None:
        attempt = session.scalar(select(JobAttempt).where(JobAttempt.lease_token == row.lease_token))
        attempt.status, attempt.finished_at = status, now
        attempt.stage = row.stage
        attempt.error_code, attempt.error_message = code, message
    row.status, row.finished_at, row.result = status, now, result
    row.error_code, row.error_message = code, message
    row.lease_token = row.lease_expires_at = row.worker_id = None
    if status == "succeeded":
        row.progress, row.stage = 100, "completed"
    if row.kind == "agent_run" and status in ("failed", "cancelled"):
        # 分析失败、取消和租约到期也保存有序终态事件，既有任务逻辑保持不变。
        from .agent_models import AgentRun, AgentEvent
        run = session.scalar(select(AgentRun).where(AgentRun.job_id == row.id).with_for_update())
        if run is not None:
            run.event_sequence += 1
            session.add(AgentEvent(run_id=run.id, sequence=run.event_sequence, kind=status,
                details={"code": code, "message": message, "stage": row.stage}))
    audit(session, None, f"job.{status}", "job", row.id, row.request_id,
          {"kind": row.kind, "attempt": row.attempt_count, "stage": row.stage, "error_code": code})


def cancel_job(session, principal, job_id, request_id):
    row = owned_job(session, principal, job_id, lock=True)
    if row.status in TERMINAL or row.cancel_requested_at is not None:
        return job_view(session, row)
    now = db_now(session)
    row.cancel_requested_at = now
    if row.status in ("queued", "waiting_review"):
        _close(session, row, "cancelled", now, "JOB_CANCELLED", "任务已在排队或待审核边界取消。")
    audit(session, principal.user.id, "job.cancel_requested", "job", row.id, request_id, {"status": row.status})
    session.flush()
    return job_view(session, row)


def _owner_allowed(session, row):
    user = session.get(User, row.owner_id)
    if row.kind == "report_export":
        from .delivery_service import execution_export
        try:
            export, _ = execution_export(session, UUID(row.payload["export_id"]))
            return export.job_id == row.id and export.creator_id == row.owner_id
        except ServiceError:
            return False
    if row.kind == "agent_run":
        return user is not None and user.is_active and "analysis:create" in permissions([role.name for role in user.roles])
    permission = "knowledge:manage" if row.kind in ("document_parse", "document_visual_parse", "knowledge_index", "knowledge_answer") else "system:read"
    return user is not None and user.is_active and not user.is_guest and \
        permission in permissions([role.name for role in user.roles])


def recover_expired(session, replayable_kinds):
    """只读任务按原逻辑重领；新一键流程从 Checkpoint 和动作记录继续。"""
    now = db_now(session)
    rows = session.scalars(select(Job).where(
        Job.status.in_(["queued", "running"]),
        (Job.deadline_at <= now) | ((Job.status == "running") & (Job.lease_expires_at <= now)))
        .order_by(Job.created_at, Job.id).limit(50).with_for_update(skip_locked=True)).all()
    for row in rows:
        recoverable = False
        if row.kind == "agent_run":
            from .agent_models import AgentRun
            run = session.scalar(select(AgentRun).where(AgentRun.job_id == row.id))
            recoverable = run is not None and run.workflow == "one_click" and run.recovery_version == "one-click-v1"
        if row.cancel_requested_at is not None:
            _close(session, row, "cancelled", now, "JOB_CANCELLED", "已在任务边界确认取消。")
        elif row.deadline_at <= now:
            _close(session, row, "failed", now, "JOB_TIMEOUT", "任务已超过提交时固定的总时限。")
        elif not _owner_allowed(session, row):
            _close(session, row, "failed", now, "JOB_OWNER_FORBIDDEN", "任务所属账号已停用或不再具有执行权限。")
        elif (row.kind not in replayable_kinds and not recoverable) or row.attempt_count >= row.max_attempts:
            _close(session, row, "failed", now, "JOB_LEASE_EXPIRED", "Worker 租约到期且不允许重领或已达到尝试上限。")
        else:
            attempt = session.scalar(select(JobAttempt).where(JobAttempt.lease_token == row.lease_token))
            attempt.status, attempt.finished_at, attempt.stage = "lease_expired", now, row.stage
            attempt.error_code, attempt.error_message = "JOB_LEASE_EXPIRED", "Worker 失联，旧执行租约已作废。"
            row.status, row.stage = "queued", "recovering"
            if not recoverable:
                row.progress = 0
            row.lease_token = row.lease_expires_at = row.worker_id = None
            audit(session, None, "job.requeued", "job", row.id, row.request_id, {"attempt": row.attempt_count})
    return len(rows)


def claim_job(session, worker_id, kinds, config):
    now = db_now(session)
    row = session.scalar(select(Job).where(Job.status == "queued", Job.kind.in_(kinds), Job.deadline_at > now)
                         .order_by(Job.created_at, Job.id).limit(1).with_for_update(skip_locked=True))
    if row is None:
        return None
    if not _owner_allowed(session, row):
        _close(session, row, "failed", now, "JOB_OWNER_FORBIDDEN", "任务所属账号已停用或不再具有执行权限。")
        return None
    token = uuid4()
    row.status, row.stage, row.worker_id, row.lease_token = "running", "claimed", worker_id, token
    row.attempt_count += 1
    if row.started_at is None:
        row.started_at = now
    row.heartbeat_at = now
    row.lease_expires_at = min(now + timedelta(seconds=config.lease_seconds), row.deadline_at)
    session.add(JobAttempt(job_id=row.id, number=row.attempt_count, worker_id=worker_id, lease_token=token,
                           started_at=now, heartbeat_at=now))
    audit(session, None, "job.claimed", "job", row.id, row.request_id,
          {"worker_id": worker_id, "attempt": row.attempt_count})
    return Lease(row.id, token, row.kind, dict(row.payload), row.attempt_count)


def _live(session, lease):
    row = session.scalar(select(Job).where(Job.id == lease.job_id).with_for_update())
    now = db_now(session)
    if row is None or row.status != "running" or row.lease_token != lease.token:
        return None, now
    if row.cancel_requested_at is not None:
        _close(session, row, "cancelled", now, "JOB_CANCELLED", "已在任务边界确认取消。")
        return None, now
    if row.deadline_at <= now:
        _close(session, row, "failed", now, "JOB_TIMEOUT", "任务已超过总时限。")
        return None, now
    if row.lease_expires_at <= now:
        return None, now
    if not _owner_allowed(session, row):
        _close(session, row, "failed", now, "JOB_OWNER_FORBIDDEN", "任务所属账号已停用或不再具有执行权限。")
        return None, now
    return row, now


def heartbeat(session, lease, config, stage=None, progress=None):
    row, now = _live(session, lease)
    if row is None:
        return False
    row.heartbeat_at = now
    row.lease_expires_at = min(now + timedelta(seconds=config.lease_seconds), row.deadline_at)
    if stage is not None:
        row.stage = stage
        row.progress = progress
    attempt = session.scalar(select(JobAttempt).where(JobAttempt.lease_token == lease.token))
    attempt.heartbeat_at, attempt.stage = now, row.stage
    return True


def finish_job(session, lease, result=None, error_code=None, error_message=None):
    row, now = _live(session, lease)
    if row is None:
        return False
    _close(session, row, "failed" if error_code else "succeeded", now, error_code, error_message, result)
    return True
