"""目录展示与可恢复删除，不改变不可变正文或历史模型结果。"""
from uuid import UUID
from sqlalchemy import select, func
from .auth_service import ServiceError, audit
from .document_service import authorize, version_view, _document
from .document_models import Document, DocumentVersion
from .index_models import KnowledgeCatalog, KnowledgeSnapshot
from .index_service import member_valid
from .job_models import Job
from .qa_models import KnowledgeAnswer
from .parse_models import DocumentPage

CATEGORIES = {"数据与设备", "预测与趋势", "模型说明", "知识助手与管理", "验收资料"}


def version_stage(version):
    if not version["is_active"]:
        return "已停用"
    if version["status"] == "failed" or version["parse_status"] == "failed":
        return "失败"
    if version["status"] == "uploading" or version["parse_status"] in ("queued", "running"):
        return "处理中"
    if version["parse_status"] != "succeeded":
        return "待解析"
    if version["latest_parse"]["review_status"] != "approved":
        return "待审核"
    # 最新解析的发布状态单独判断，不能把同版本旧解析的发布当成新解析已发布。
    return "可用于问答" if version["latest_parse"]["knowledge_status"] == "published" else "待发布"


def catalog(session, principal, offset, limit, search="", category="", state="", archived=False):
    authorize(session, principal)
    query = select(Document).where(Document.archived_at.is_not(None) if archived else Document.archived_at.is_(None))
    if category:
        if category not in CATEGORIES:
            raise ServiceError(422, "CATEGORY_INVALID", "资料分类不合法。")
        query = query.where(Document.category == category)
    pointer = session.get(KnowledgeCatalog, 1)
    snapshot = session.get(KnowledgeSnapshot, pointer.current_id) if pointer.current_id else None
    members = {m["document_id"]: m for m in snapshot.members if member_valid(session, m)} if snapshot else {}
    items = []
    # 状态从上传、有效审核和当前发布共同推导；先全量筛选，再分页。
    for doc in session.scalars(query.order_by(Document.created_at.desc(), Document.id)):
        latest = session.scalar(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.number.desc()).limit(1))
        value = version_view(session, latest)
        member = members.get(str(doc.id))
        title = member["source_metadata"]["title"] if member else value["effective_source_metadata"]["title"]
        if search.strip() and search.strip().casefold() not in (title + " " + value["effective_source_metadata"]["title"]).casefold():
            continue
        stage = version_stage(value)
        available = member is not None
        if state == "published" and not available or state and state != "published" and stage != state:
            continue
        # 简介取问答使用版本的正文首段，不修改来源元数据或历史证据。
        parse_id = member["parse_id"] if member else value["latest_parse"]["parse_id"] if value["parse_status"] == "succeeded" else None
        description = "尚无可读正文，请完成解析。"
        if parse_id:
            first_page = session.scalar(select(DocumentPage).where(DocumentPage.parse_id == UUID(parse_id)).order_by(DocumentPage.number).limit(1))
            if first_page is None:
                raise ServiceError(503, "DOCUMENT_PAGE_UNAVAILABLE", "解析成功记录缺少正文页面，请核对解析记录。")
            body = "\n".join(line for line in first_page.text.splitlines() if not line.lstrip().startswith("#"))
            paragraphs = [part.strip() for part in body.split("\n\n") if part.strip()]
            description = "正文仅包含标题。" if not paragraphs else paragraphs[0][:120] + ("…" if len(paragraphs[0]) > 120 else "")
        items.append({"document_id": str(doc.id), "title": title, "category": doc.category,
            "archived_at": doc.archived_at, "created_at": doc.created_at, "latest_version": value,
            "published_version": member, "available": available, "stage": stage,
            "description": description})
    return {"items": items[offset:offset+limit], "total": len(items), "offset": offset, "limit": limit}


def manage_directory(session, principal, document_id, category, archived, request_id):
    authorize(session, principal)
    # 与发布共用目录锁；归档不能在索引执行中撤销其输入。
    session.scalar(select(KnowledgeCatalog).where(KnowledgeCatalog.id == 1).with_for_update())
    doc = _document(session, document_id, lock=True)
    if category is not None:
        if category not in CATEGORIES:
            raise ServiceError(422, "CATEGORY_INVALID", "资料分类不合法。")
        doc.category = category
    if archived is not None and archived != (doc.archived_at is not None):
        if session.scalar(select(Job.id).where(Job.kind == "knowledge_index", Job.status.in_(["queued", "running"])).limit(1)):
            raise ServiceError(409, "PUBLICATION_PENDING", "索引发布尚未结束，请完成或取消发布后归档资料。")
        # 与问答最终来源核对共用版本锁，避免核对后、保存前发生归档。
        list(session.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id)
             .order_by(DocumentVersion.id).with_for_update()))
        doc.archived_at = session.scalar(select(func.clock_timestamp())) if archived else None
    audit(session, principal.user.id, "document.directory_changed", "document", doc.id, request_id,
          {"category": doc.category, "archived": doc.archived_at is not None})
    return {"document_id": str(doc.id), "category": doc.category, "archived_at": doc.archived_at}


def conversation_lifecycle(session, principal, conversation_id, restore, request_id):
    from .qa_service import owner_lock
    from .conversation_service import owned_conversation, conversation_view
    authorize(session, principal); owner_lock(session, principal)
    row = owned_conversation(session, principal, conversation_id, lock=True, include_deleted=True)
    if not restore and session.scalar(select(Job.id).join(KnowledgeAnswer, KnowledgeAnswer.job_id == Job.id).where(
            KnowledgeAnswer.conversation_id == row.id, Job.status.in_(["queued", "running"])).limit(1)):
        raise ServiceError(409, "CONVERSATION_PENDING", "本会话仍有执行中的任务，请先取消并等待结束。")
    if restore != (row.deleted_at is None):
        row.deleted_at = None if restore else session.scalar(select(func.clock_timestamp()))
        audit(session, principal.user.id, "conversation.restored" if restore else "conversation.deleted",
              "knowledge_conversation", row.id, request_id, {})
    return conversation_view(row)


def job_lifecycle(session, principal, ids, restore, request_id):
    from .job_service import owned_job, TERMINAL
    results = []
    for job_id in sorted(set(ids), key=str):
        row = owned_job(session, principal, job_id, lock=True, include_deleted=True)
        if not restore and row.status not in TERMINAL:
            results.append({"job_id": str(row.id), "changed": False, "reason": "执行中，请先取消并等待结束"})
            continue
        if restore != (row.deleted_at is None):
            row.deleted_at = None if restore else session.scalar(select(func.clock_timestamp()))
            audit(session, principal.user.id, "job.restored" if restore else "job.deleted", "job", row.id, request_id, {})
        results.append({"job_id": str(row.id), "changed": True, "reason": None})
    return {"items": results}


def answer_navigation(session, principal, answer_id):
    from .qa_service import owned_answer
    row = owned_answer(session, principal, answer_id)
    return {"answer_id": str(row.id), "conversation_id": str(row.conversation_id) if row.conversation_id else None}
