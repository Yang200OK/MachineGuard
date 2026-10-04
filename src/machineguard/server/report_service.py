"""审核只恢复已保存的候选；正式报告与任务成功在同一事务提交。"""

from datetime import timedelta
from hashlib import sha256
import json
from uuid import UUID
from sqlalchemy import and_, func, select
from . import agent_service, job_service
from .agent_models import AgentRun
from .auth_service import Principal, ServiceError, audit
from .job_models import Job
from .models import User, SessionRecord
from .report_models import ReportReview, FinalReport
from .security import permissions


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def is_admin(principal):
    return any(role.name == "admin" for role in principal.user.roles)


def reviewer_allowed(principal, row):
    principal.require("reports:review")
    if not is_admin(principal) and row.assigned_reviewer_id != principal.user.id:
        raise ServiceError(404, "REVIEW_NOT_FOUND", "审核记录不存在或未分配给当前身份。")


def review_row(session, principal, run_id, lock=False):
    row = session.get(AgentRun, run_id)
    if row is None or row.workflow != "one_click" or row.recovery_version != "one-click-v1" or row.review_started_at is None:
        raise ServiceError(404, "REVIEW_NOT_FOUND", "此运行没有可审核的报告候选。")
    reviewer_allowed(principal, row)
    # 与取消、领取和完成保持相同锁顺序，避免并发决定或死锁。
    job = session.scalar(select(Job).where(Job.id == row.job_id).with_for_update()) if lock else session.get(Job, row.job_id)
    if lock:
        row = session.scalar(select(AgentRun).where(AgentRun.id == run_id).with_for_update().execution_options(populate_existing=True))
        reviewer_allowed(principal, row)
    if job.deleted_at is not None:
        raise ServiceError(404, "REVIEW_NOT_FOUND", "审核记录已移入回收站。")
    return row, job


def decision_view(session, decision):
    user = session.get(User, decision.reviewer_id)
    return {"id": str(decision.id), "decision": decision.decision, "comment": decision.comment,
        "reviewer_id": str(user.id), "reviewer_name": user.display_name, "created_at": decision.created_at,
        "candidate_sha256": decision.candidate_sha256, "checkpoint_id": decision.checkpoint_id}


def review_summary(session, row):
    if row.review_started_at is None:
        return None
    decision = session.get(ReportReview, row.id)
    report = session.scalar(select(FinalReport).where(FinalReport.run_id == row.id))
    return {"status": decision.decision if decision else "pending", "started_at": row.review_started_at,
        "approval_available": session.get(Job, row.job_id).status == "waiting_review" and decision is None,
        "assigned_reviewer_id": str(row.assigned_reviewer_id) if row.assigned_reviewer_id else None,
        "candidate_sha256": digest(row.result) if row.result else None,
        "decision": decision_view(session, decision) if decision else None,
        "report_id": str(report.id) if report else None}


def review_view(session, row):
    owner = session.get(User, row.owner_id)
    return {"run_id": str(row.id), "owner_name": owner.display_name, "question": row.question,
        "candidate": row.result["report_candidate"], "review": review_summary(session, row),
        "job": job_service.job_view(session, session.get(Job, row.job_id)),
        "model_calls": row.model_calls, "tool_calls": row.tool_calls,
        "checkpoint_id": row.checkpoint_id}


def list_reviews(session, principal, offset, limit, view="active"):
    principal.require("reports:review")
    query = select(AgentRun).join(Job, AgentRun.job_id == Job.id).where(AgentRun.review_started_at.is_not(None), Job.deleted_at.is_(None))
    if not is_admin(principal):
        query = query.where(AgentRun.assigned_reviewer_id == principal.user.id)
    # 保存完成才移出待办；批准后的排队、执行和失败仍需要可见的处理状态。
    query = query.outerjoin(ReportReview, ReportReview.run_id == AgentRun.id).outerjoin(FinalReport, FinalReport.run_id == AgentRun.id)
    active = and_(FinalReport.id.is_(None), Job.status != "cancelled",
                  (ReportReview.decision.is_(None) | (ReportReview.decision == "approved")))
    if view == "active":
        query = query.where(active)
    elif view == "history":
        query = query.where(~active)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(AgentRun.review_started_at.desc(), AgentRun.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [review_view(session, row) for row in rows], "total": total, "offset": offset, "limit": limit}


def reviewers(session, principal):
    if not is_admin(principal):
        raise ServiceError(403, "PERMISSION_DENIED", "仅管理员可分配审核员。")
    users = session.scalars(select(User).where(User.is_active.is_(True), User.is_guest.is_(False)).order_by(User.display_name, User.id)).all()
    return {"items": [{"id": str(user.id), "display_name": user.display_name} for user in users
        if "reports:review" in permissions([role.name for role in user.roles])]}


def assign(session, principal, run_id, reviewer_id, request_id):
    if not is_admin(principal):
        raise ServiceError(403, "PERMISSION_DENIED", "仅管理员可分配审核员。")
    row, job = review_row(session, principal, run_id, True)
    if job.status != "waiting_review" or session.get(ReportReview, row.id) is not None:
        raise ServiceError(409, "REVIEW_CLOSED", "候选已不在待审核状态，不能变更分配。")
    reviewer = session.get(User, reviewer_id) if reviewer_id else None
    if reviewer_id and (reviewer is None or not reviewer.is_active or reviewer.is_guest or "reports:review" not in permissions([role.name for role in reviewer.roles])):
        raise ServiceError(422, "REVIEWER_INVALID", "必须选择启用且具有报告审核权限的正式账号。")
    row.assigned_reviewer_id = reviewer_id
    audit(session, principal.user.id, "report.assigned", "agent_run", row.id, request_id,
          {"reviewer_id": str(reviewer_id) if reviewer_id else None})
    return review_view(session, row)


def execution_principal(session, row, decision):
    """人工决定授权后只装配报告；不以提交会话存活作为等待时限。"""
    user = session.get(User, row.owner_id)
    record = session.get(SessionRecord, row.session_id)
    reviewer = session.get(User, decision.reviewer_id)
    if row.workflow != "one_click" or row.recovery_version != "one-click-v1" or decision.run_id != row.id:
        raise ServiceError(409, "REVIEW_RESUME_INVALID", "人工决定与固定一键恢复流程不一致。")
    if user is None or not user.is_active or record is None or record.user_id != user.id or reviewer is None or not reviewer.is_active:
        raise ServiceError(403, "REVIEW_IDENTITY_CHANGED", "提交人或审核人已停用，不能恢复报告。")
    names = [role.name for role in reviewer.roles]
    if "reports:review" not in permissions(names) or ("admin" not in names and row.assigned_reviewer_id != reviewer.id):
        raise ServiceError(403, "REVIEW_PERMISSION_CHANGED", "审核人的权限或分配已变化，不能保存报告。")
    # 此身份仅供原始记录校验。工具仍独立要求 fresh_principal，恢复图禁止执行工具节点。
    principal = Principal(user, record)
    principal.require("analysis:create")
    return principal


def decide(session, principal, run_id, payload, request_id):
    row, job = review_row(session, principal, run_id, True)
    existing = session.get(ReportReview, row.id)
    if existing:
        if (existing.reviewer_id != principal.user.id or existing.idempotency_key != payload.idempotency_key
                or existing.decision != payload.decision or existing.comment != payload.comment
                or existing.candidate_sha256 != payload.candidate_sha256 or existing.checkpoint_id != payload.checkpoint_id):
            raise ServiceError(409, "REVIEW_CONFLICT", "此候选已有人作出决定；不能改写意见或重复决定。")
        return {**review_view(session, row), "replayed": True}
    if job.status != "waiting_review" or job.cancel_requested_at is not None:
        raise ServiceError(409, "REVIEW_CLOSED", "本运行已离开待审核状态。")
    if digest(row.result) != payload.candidate_sha256 or row.checkpoint_id != payload.checkpoint_id:
        raise ServiceError(409, "REVIEW_STALE", "候选内容或执行位置已变化，请重新读取后审核。")
    from .recovery_models import GraphCheckpoint, GraphWrite
    if session.get(GraphCheckpoint, (row.id, row.checkpoint_id)) is None or session.scalar(select(GraphWrite.run_id).where(
            GraphWrite.run_id == row.id, GraphWrite.checkpoint_id == row.checkpoint_id, GraphWrite.channel == "__interrupt__").limit(1)) is None:
        raise ServiceError(409, "AGENT_CHECKPOINT_INVALID", "候选没有可恢复的持久化中断记录。")
    decision = ReportReview(run_id=row.id, reviewer_id=principal.user.id, idempotency_key=payload.idempotency_key,
        decision=payload.decision, comment=payload.comment, candidate_sha256=payload.candidate_sha256,
        checkpoint_id=payload.checkpoint_id)
    session.add(decision); session.flush()
    if decision.decision == "approved":
        owner = execution_principal(session, row, decision)
        agent_service.check_result(session, row, row.result)
        from .report_contract import validate_one_click
        validate_one_click(session, owner, row, row.result)
    # 审核等待不占执行时间；只在首次决定时给恢复段固定时间和三次领取。
    job.status = "queued"; job.stage = "review_resume"
    job.deadline_at = job_service.db_now(session) + timedelta(seconds=row.config_snapshot["agent"]["timeout_seconds"])
    job.max_attempts = min(10, job.attempt_count + 3)
    agent_service.add_event(session, row, "review_decided", decision_view(session, decision))
    audit(session, principal.user.id, "report.reviewed", "agent_run", row.id, request_id,
        {"review_id": str(decision.id), "decision": decision.decision, "candidate_sha256": decision.candidate_sha256,
         "checkpoint_id": decision.checkpoint_id, "comment": decision.comment})
    return {**review_view(session, row), "replayed": False}


def checked_resume(session, row, result, review_id):
    decision = session.get(ReportReview, row.id)
    if decision is None or str(decision.id) != review_id or digest(result) != decision.candidate_sha256 or digest(row.result) != decision.candidate_sha256:
        raise ServiceError(409, "REVIEW_RESUME_INVALID", "恢复输入与已保存的人工决定或候选不一致。")
    execution_principal(session, row, decision)
    return decision


def save_final(session, row, decision):
    if decision.decision != "approved":
        return None
    candidate = row.result["report_candidate"]
    # 原候选永久保留未审核身份；正式报告单独保存审核意见和完整可信字段。
    content = {**candidate, "schema": "machineguard.report.v1", "status": "approved", "title": "设备综合分析报告",
        "review": agent_service.jsonable(decision_view(session, decision)),
        "limitations": [*candidate["limitations"][:-1], "已通过人工报告审核；仍需现场有效文件和授权，不能据此自动执行维护。"]}
    report = session.scalar(select(FinalReport).where(FinalReport.run_id == row.id))
    if report:
        if report.content_sha256 != digest(content) or report.review_id != decision.id:
            raise ServiceError(409, "REPORT_CONFLICT", "正式报告已保存不同内容，不能覆盖。")
        return report
    report = FinalReport(run_id=row.id, owner_id=row.owner_id, review_id=decision.id,
        prediction_id=UUID(candidate["prediction"]["prediction_id"]), content=content, content_sha256=digest(content))
    session.add(report); session.flush()
    agent_service.add_event(session, row, "report_saved", {"report_id": str(report.id), "content_sha256": report.content_sha256})
    audit(session, decision.reviewer_id, "report.saved", "report", report.id, session.get(Job, row.job_id).request_id,
          {"run_id": str(row.id), "review_id": str(decision.id), "content_sha256": report.content_sha256})
    return report


def report_view(report, session=None):
    result = {"report_id": str(report.id), "run_id": str(report.run_id), "content": report.content,
        "content_sha256": report.content_sha256, "created_at": report.created_at}
    if session is not None:
        # 已通过正式报告访问校验的身份可查看其原始提交依据，不获得审核或知识管理权限。
        row = session.get(AgentRun, report.run_id)
        owner = session.get(User, row.owner_id)
        result["provenance"] = {"candidate": row.result["report_candidate"], "question": row.question,
            "owner_name": owner.display_name, "submitted_at": session.get(Job, row.job_id).created_at,
            "model_calls": row.model_calls, "tool_calls": row.tool_calls}
    return result


def authorized_report(session, principal, report_id):
    report = session.get(FinalReport, report_id)
    if report is None:
        raise ServiceError(404, "REPORT_NOT_FOUND", "正式报告不存在或当前身份无权查看。")
    if report.owner_id == principal.user.id:
        principal.require("analysis:create")
    else:
        reviewer_allowed(principal, session.get(AgentRun, report.run_id))
    return report


def list_reports(session, principal, offset, limit):
    query = select(FinalReport).join(AgentRun, AgentRun.id == FinalReport.run_id)
    perms = permissions([role.name for role in principal.user.roles])
    if "reports:review" in perms:
        if not is_admin(principal):
            query = query.where((AgentRun.assigned_reviewer_id == principal.user.id) | (FinalReport.owner_id == principal.user.id))
    else:
        principal.require("analysis:create")
        query = query.where(FinalReport.owner_id == principal.user.id)
    total = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(FinalReport.created_at.desc(), FinalReport.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [report_view(row) for row in rows], "total": total, "offset": offset, "limit": limit}
