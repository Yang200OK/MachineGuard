"""会话归属、固定范围、顺序轮次和服务器历史快照。"""

import json
from uuid import UUID
from sqlalchemy import func, select
from .auth_service import ServiceError, audit
from .conversation_config import ConversationConfig, load_conversation_configuration
from .device_service import authorized_engine
from .document_service import authorize
from .index_config import digest
from .index_models import KnowledgeCatalog, KnowledgeSnapshot
from .job_models import Job
from .qa_models import KnowledgeAnswer, KnowledgeConversation
from .qa_provider import tokens


def owned_conversation(session, principal, conversation_id, lock=False, include_deleted=False):
    authorize(session, principal)
    query = select(KnowledgeConversation).where(KnowledgeConversation.id == conversation_id,
        KnowledgeConversation.owner_id == principal.user.id)
    if not include_deleted:
        query = query.where(KnowledgeConversation.deleted_at.is_(None))
    row = session.scalar(query.with_for_update() if lock else query)
    if row is None:
        raise ServiceError(404, "CONVERSATION_NOT_FOUND", "会话不存在或不属于当前身份。")
    return row


def device_context(session, principal, engine_key, end_cycle):
    if engine_key is None:
        return None
    engine, dataset = authorized_engine(session, principal, engine_key)
    if not 1 <= end_cycle <= engine.last_cycle:
        raise ServiceError(422, "CYCLE_INVALID", "会话周期超出该设备的已观测范围。")
    return {"engine_key": engine.engine_key, "end_cycle": end_cycle,
            "dataset_id": str(dataset.id), "data_version": dataset.source_sha256}


def validate_device(session, principal, frozen):
    if frozen is not None and device_context(session, principal, frozen["engine_key"], frozen["end_cycle"]) != frozen:
        raise ServiceError(409, "QA_DEVICE_CHANGED", "会话设备数据依据已变化，请建立新会话。")


def conversation_view(row):
    return {"conversation_id": str(row.id), "title": row.title, "snapshot_id": str(row.snapshot_id),
        "equipment_type": row.equipment_type, "device_context": row.device_context, "revision": row.revision,
        "created_at": row.created_at.isoformat(), "deleted_at": row.deleted_at,
        "config_snapshot": row.config_snapshot, "config_sha256": row.config_sha256}


def create_conversation(session, principal, payload, request_id):
    # 同一用户的创建与问答提交共用事务锁，丢失响应后只确认原会话。
    from .qa_service import owner_lock
    authorize(session, principal); owner_lock(session, principal)
    fingerprint = digest(payload.model_dump(mode="json"))
    row = session.scalar(select(KnowledgeConversation).where(KnowledgeConversation.owner_id == principal.user.id,
        KnowledgeConversation.idempotency_key == payload.idempotency_key))
    if row:
        if row.deleted_at is not None:
            raise ServiceError(409, "CONVERSATION_DELETED", "原会话已删除，请从回收站恢复，不能用原键创建新会话。")
        if row.request_fingerprint != fingerprint:
            raise ServiceError(409, "CONVERSATION_IDEMPOTENCY_CONFLICT", "原请求键已绑定另一组会话参数。")
        return {**conversation_view(row), "replayed": True}
    snapshot_id = payload.snapshot_id or session.get(KnowledgeCatalog, 1).current_id
    snapshot = session.get(KnowledgeSnapshot, snapshot_id) if snapshot_id else None
    if snapshot is None or snapshot.published_at is None:
        raise ServiceError(409, "KNOWLEDGE_NOT_PUBLISHED", "请先发布完整知识快照。")
    frozen, config_hash = load_conversation_configuration().frozen()
    row = KnowledgeConversation(owner_id=principal.user.id, snapshot_id=snapshot_id, title=payload.title,
        equipment_type=payload.equipment_type, device_context=device_context(session, principal, payload.engine_key, payload.end_cycle),
        config_snapshot=frozen, config_sha256=config_hash, idempotency_key=payload.idempotency_key,
        request_fingerprint=fingerprint, revision=0)
    session.add(row); session.flush()
    audit(session, principal.user.id, "knowledge.conversation_created", "knowledge_conversation", row.id, request_id,
          {"snapshot_id": str(snapshot_id), "config_sha256": config_hash})
    return {**conversation_view(row), "replayed": False}


def list_conversations(session, principal, offset, limit, search="", deleted=False):
    authorize(session, principal)
    query = select(KnowledgeConversation).where(KnowledgeConversation.owner_id == principal.user.id)
    query = query.where(KnowledgeConversation.deleted_at.is_not(None) if deleted else KnowledgeConversation.deleted_at.is_(None))
    if search.strip():
        # 搜索词按文字匹配，百分号与下划线不作为通配符。
        literal = search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(KnowledgeConversation.title.ilike(f"%{literal}%", escape="\\"))
    count = session.scalar(select(func.count()).select_from(query.subquery()))
    rows = session.scalars(query.order_by(KnowledgeConversation.created_at.desc(), KnowledgeConversation.id.desc()).offset(offset).limit(limit)).all()
    return {"items": [conversation_view(row) for row in rows], "total": count, "offset": offset, "limit": limit}


def rename_conversation(session, principal, conversation_id, title, expected_title, request_id):
    row = owned_conversation(session, principal, conversation_id, lock=True)
    authorize(session, principal)
    # 只更改显示名称；原创建请求指纹、固定范围和问答轮次保持不变。
    if row.title == title:
        return conversation_view(row)
    if row.title != expected_title:
        raise ServiceError(409, "CONVERSATION_TITLE_CONFLICT", "会话名称已变化，请刷新后重新命名。")
    before = row.title
    row.title = title
    audit(session, principal.user.id, "knowledge.conversation_renamed", "knowledge_conversation", row.id,
          request_id, {"before": before, "after": title})
    session.flush()
    return conversation_view(row)


def detail(session, principal, conversation_id):
    from .qa_service import answer_view
    row = owned_conversation(session, principal, conversation_id)
    # 会话总轮数有硬上限，详情不会无限加载历史。
    turns = session.scalars(select(KnowledgeAnswer).where(KnowledgeAnswer.conversation_id == row.id,
        KnowledgeAnswer.owner_id == principal.user.id).order_by(KnowledgeAnswer.turn_number)).all()
    return {**conversation_view(row), "turns": [answer_view(session, turn) for turn in turns]}


def prepare_turn(session, principal, payload):
    row = owned_conversation(session, principal, payload.conversation_id, lock=True)
    config = ConversationConfig.model_validate(row.config_snapshot)
    if config.frozen()[1] != row.config_sha256:
        raise ServiceError(409, "QA_CONFIG_CHANGED", "会话配置指纹不一致。")
    if row.revision != payload.expected_revision:
        raise ServiceError(409, "CONVERSATION_REVISION_CONFLICT", "会话已更新，请刷新后提交新一轮。")
    if row.revision >= config.max_turns:
        raise ServiceError(429, "CONVERSATION_TURN_LIMIT", "会话轮数已达到上限，请建立新会话。")
    pending = session.scalar(select(KnowledgeAnswer.id).join(Job).where(KnowledgeAnswer.conversation_id == row.id,
        Job.status.in_(["queued", "running"])).limit(1))
    if pending:
        raise ServiceError(409, "CONVERSATION_TURN_PENDING", "当前会话上一轮尚未完成。")
    validate_device(session, principal, row.device_context)
    previous = session.scalars(select(KnowledgeAnswer).join(Job).where(KnowledgeAnswer.conversation_id == row.id,
        KnowledgeAnswer.owner_id == principal.user.id, Job.status == "succeeded",
        KnowledgeAnswer.result["status"].astext == "answered").order_by(
        KnowledgeAnswer.turn_number.desc()).limit(config.history_turns)).all()
    history = []
    for turn in reversed(previous):
        # 只保存原问题和通过校验的逐字引用；历史生成结论不充当新证据。
        quotes = []
        for claim in turn.result["claims"]:
            for citation in claim["citations"]:
                quote = {"text": citation["quote"], "version_id": citation["version_id"]}
                if quote not in quotes:
                    quotes.append(quote)
        rewrite = turn.trace.get("rewrite", {})
        resolved = rewrite.get("resolved_question") if rewrite.get("status") in {"resolved", "unchanged"} else None
        history.append({"turn_number": turn.turn_number, "question": turn.question, "resolved_question": resolved,
                        "reference_topic": rewrite.get("topic_question") or resolved, "quotes": quotes})
    latest = session.scalar(select(KnowledgeAnswer).join(Job).where(KnowledgeAnswer.conversation_id == row.id,
        KnowledgeAnswer.owner_id == principal.user.id, Job.status == "succeeded").order_by(KnowledgeAnswer.turn_number.desc()).limit(1))
    # 拒答不充当知识依据，但保留最近一次拒答意图，避免“为什么”误指向更早的成功回答。
    latest_refusal = {"question": latest.question, "reason": latest.result["reason"],
                      "guidance": latest.result.get("guidance")} if latest and latest.result["status"] == "refused" else None
    context = {"config": row.config_snapshot, "config_sha256": row.config_sha256,
               "device": row.device_context, "history": history, "latest_refusal": latest_refusal}
    if tokens(json.dumps({"history": history, "device": row.device_context, "latest_refusal": latest_refusal}, ensure_ascii=False)) > config.max_context_tokens:
        raise ServiceError(422, "QA_CONTEXT_BUDGET_EXCEEDED", "最近历史超过会话上下文预算，请建立新会话或明确提问。")
    row.revision += 1
    return row, context, config


def validate_context(session, principal, answer, lock=False):
    from .qa_service import assert_evidence_current
    frozen = answer.context_snapshot
    if frozen is None:
        return
    row = owned_conversation(session, principal, answer.conversation_id)
    if row.snapshot_id != answer.snapshot_id or row.equipment_type != answer.equipment_type:
        raise ServiceError(409, "QA_SCOPE_CHANGED", "会话固定范围与本轮不一致。")
    config = ConversationConfig.model_validate(frozen["config"])
    if config.frozen()[1] != frozen["config_sha256"] or frozen["config_sha256"] != row.config_sha256:
        raise ServiceError(409, "QA_CONFIG_CHANGED", "冻结会话配置指纹不一致。")
    if frozen["device"] != row.device_context or len(frozen["history"]) > config.history_turns:
        raise ServiceError(409, "QA_CONTEXT_INVALID", "冻结会话上下文不符合范围。")
    validate_device(session, principal, frozen["device"])
    assert_evidence_current(session, answer, [quote for turn in frozen["history"] for quote in turn["quotes"]], lock=lock)
    budget_context = {"history": frozen["history"], "device": frozen["device"]}
    if "latest_refusal" in frozen:
        budget_context["latest_refusal"] = frozen["latest_refusal"]
    if tokens(json.dumps(budget_context, ensure_ascii=False)) > config.max_context_tokens:
        raise ServiceError(422, "QA_CONTEXT_BUDGET_EXCEEDED", "冻结上下文超过预算。")
