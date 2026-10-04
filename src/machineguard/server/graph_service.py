"""复用资料可用性和历史引用校验，人工候选不绕过来源与审核。"""

from hashlib import sha256
import json
from uuid import UUID

from sqlalchemy import func, select, text

from .auth_service import ServiceError, audit
from .document_models import Document, DocumentVersion
from .document_service import authorize
from .graph_models import GraphAction, GraphEntity, GraphRelation
from .index_models import KnowledgeCatalog, KnowledgeSnapshot, SnapshotChunk
from .index_service import member_valid
from .models import User
from .parse_models import DocumentChunk
from .qa_source import checked_citation_source

MAX_RELATIONS = 1000


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def current_sources(session):
    """只读取当前发布中仍通过既有审核规则的成员，不修改任何知识状态。"""
    catalog = session.get(KnowledgeCatalog, 1)
    if catalog is None or catalog.current_id is None:
        return None, {}
    snapshot = session.get(KnowledgeSnapshot, catalog.current_id)
    if snapshot is None or snapshot.published_at is None:
        raise ServiceError(409, "GRAPH_SNAPSHOT_UNAVAILABLE", "当前知识快照不可用，请核对发布记录。")
    members = {m["version_id"]: m for m in snapshot.members if member_valid(session, m)}
    rows = session.scalars(select(SnapshotChunk).where(SnapshotChunk.snapshot_id == snapshot.id)
        .order_by(SnapshotChunk.version_id, SnapshotChunk.location["char_start"].as_integer(), SnapshotChunk.chunk_id)).all()
    return snapshot, {row.chunk_id: (row, members[str(row.version_id)]) for row in rows if str(row.version_id) in members}


def sources(session, principal, offset, limit):
    authorize(session, principal)
    snapshot, allowed = current_sources(session)
    rows = list(allowed.values())
    items = []
    for frozen, member in rows[offset:offset + limit]:
        chunk = session.get(DocumentChunk, frozen.chunk_id)
        if chunk is None or chunk.sha256 != frozen.sha256 or chunk.text != frozen.text or sha256(chunk.text.encode("utf-8")).hexdigest() != frozen.sha256:
            raise ServiceError(409, "GRAPH_SOURCE_INVALID", "知识切片与已发布正文不一致，不能摘录。")
        items.append({"chunk_id": str(chunk.id), "snapshot_id": str(snapshot.id), "chunk_sha256": frozen.sha256,
            "title": session.get(Document, chunk.source_id).title, "text": chunk.text, "page": chunk.page,
            "section": chunk.section, "document_id": str(chunk.source_id), "version_id": str(chunk.document_version),
            "document_version": member["version_number"], "source_metadata": frozen.source_metadata})
    return {"items": items, "total": len(rows), "offset": offset, "limit": limit,
        "snapshot_id": str(snapshot.id) if snapshot else None}


def entity_view(session, entity):
    return {"entity_id": str(entity.id), "name": entity.name, "kind": entity.kind,
        "namespace": "document:" + str(entity.document_id), "document_id": str(entity.document_id),
        "document_title": session.get(Document, entity.document_id).title}


def entities(session, principal, offset, limit):
    authorize(session, principal)
    rows = session.scalars(select(GraphEntity).order_by(GraphEntity.created_at, GraphEntity.id).offset(offset).limit(limit)).all()
    return {"items": [entity_view(session, row) for row in rows],
        "total": session.scalar(select(func.count()).select_from(GraphEntity)), "offset": offset, "limit": limit}


def available(row, allowed):
    entry = allowed.get(row.chunk_id)
    return bool(entry and entry[0].sha256 == row.chunk_sha256 and entry[1] == row.source_basis)


def relation_view(session, row, allowed):
    source_available = available(row, allowed)
    return {"relation_id": str(row.id), "subject": entity_view(session, session.get(GraphEntity, row.subject_id)),
        "object": entity_view(session, session.get(GraphEntity, row.object_id)), "predicate": row.predicate,
        "snapshot_id": str(row.snapshot_id), "citation": row.citation, "chunk_sha256": row.chunk_sha256,
        "note": row.note, "status": row.status, "revision": row.revision, "source_available": source_available,
        "usable": row.status == "approved" and source_available,
        "unavailable_reason": None if source_available else "当前发布不再包含相同有效来源或其审核依据已变化。",
        "creator": session.get(User, row.creator_id).display_name, "created_at": row.created_at.isoformat()}


def relations(session, principal, status, available_only, offset, limit):
    authorize(session, principal)
    snapshot, allowed = current_sources(session)
    query = select(GraphRelation)
    if available_only:
        # 对外可用集合强制要求已批准，不允许利用筛选组合读到待审关系。
        query = query.where(GraphRelation.status == "approved")
    if status != "all":
        query = query.where(GraphRelation.status == status)
    rows = session.scalars(query.order_by(GraphRelation.created_at.desc(), GraphRelation.id)).all()
    if available_only:
        rows = [row for row in rows if available(row, allowed)]
    return {"items": [relation_view(session, row, allowed) for row in rows[offset:offset + limit]],
        "total": len(rows), "offset": offset, "limit": limit, "snapshot_id": str(snapshot.id) if snapshot else None}


def get_relation(session, relation_id, lock=False):
    query = select(GraphRelation).where(GraphRelation.id == relation_id)
    if lock:
        query = query.with_for_update()
    row = session.scalar(query)
    if row is None:
        raise ServiceError(404, "GRAPH_RELATION_NOT_FOUND", "关系记录不存在。")
    return row


def detail(session, principal, relation_id):
    authorize(session, principal)
    row = get_relation(session, relation_id)
    _, allowed = current_sources(session)
    actions = session.scalars(select(GraphAction).where(GraphAction.relation_id == row.id)
        .order_by(GraphAction.created_at, GraphAction.id)).all()
    return {**relation_view(session, row, allowed), "actions": [{"action_id": str(action.id), "kind": action.kind,
        "actor": session.get(User, action.actor_id).display_name, "revision": action.revision,
        "payload": action.payload, "result": action.result, "created_at": action.created_at.isoformat()} for action in actions]}


def source(session, principal, relation_id):
    authorize(session, principal)
    row = get_relation(session, relation_id)
    return checked_citation_source(session, row.citation)


def mutation_lock(session, principal):
    authorize(session, principal)
    # 初始规模限定一千条关系；统一写锁保护实体复用、去重及同一账号的请求键。
    session.execute(text("SELECT pg_advisory_xact_lock(774413008)"))
    authorize(session, principal)


def replay(session, principal, key, fingerprint):
    receipt = session.scalar(select(GraphAction).where(GraphAction.actor_id == principal.user.id,
        GraphAction.idempotency_key == UUID(key)))
    if receipt and receipt.request_fingerprint != fingerprint:
        raise ServiceError(409, "GRAPH_IDEMPOTENCY_CONFLICT", "该动作键已用于另一组图谱参数。")
    return {**receipt.result, "replayed": True} if receipt else None


def lock_source(session, principal, version_id):
    # 与现有知识发布一致：目录在前、版本在后，避免反向持锁。
    session.scalar(select(KnowledgeCatalog).where(KnowledgeCatalog.id == 1).with_for_update()
        .execution_options(populate_existing=True))
    session.scalar(select(Document).join(DocumentVersion, DocumentVersion.document_id == Document.id)
        .where(DocumentVersion.id == version_id).with_for_update(of=Document).execution_options(populate_existing=True))
    session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update()
        .execution_options(populate_existing=True))
    authorize(session, principal)


def create_entity(session, document_id, name, kind):
    row = session.scalar(select(GraphEntity).where(GraphEntity.document_id == document_id, GraphEntity.name == name))
    if row and row.kind != kind:
        raise ServiceError(409, "GRAPH_ENTITY_KIND_CONFLICT", "同一文档的同名实体已有另一类型，请核对后使用原类型。")
    if row is None:
        row = GraphEntity(document_id=document_id, name=name, kind=kind)
        session.add(row); session.flush()
    return row


def append_action(session, principal, row, request, request_id, fingerprint, kind, result):
    session.add(GraphAction(relation_id=row.id, actor_id=principal.user.id, idempotency_key=UUID(request.idempotency_key),
        request_id=request_id, request_fingerprint=fingerprint, kind=kind, revision=row.revision,
        payload=request.model_dump(mode="json", exclude={"idempotency_key"}), result=result))
    audit(session, principal.user.id, "graph." + kind, "graph_relation", row.id, request_id,
        {"status": row.status, "revision": row.revision, "chunk_id": str(row.chunk_id)})
    session.flush()


def create_relation(session, principal, request, request_id):
    fingerprint = digest({"kind": "create", "request": request.model_dump(mode="json")})
    mutation_lock(session, principal)
    previous = replay(session, principal, request.idempotency_key, fingerprint)
    if previous:
        return previous
    chunk = session.get(DocumentChunk, UUID(request.chunk_id))
    if chunk is None:
        raise ServiceError(404, "GRAPH_CHUNK_NOT_FOUND", "资料切片不存在。")
    lock_source(session, principal, chunk.document_version)
    snapshot, allowed = current_sources(session)
    entry = allowed.get(chunk.id)
    if snapshot is None or str(snapshot.id) != request.snapshot_id.lower() or entry is None:
        raise ServiceError(409, "GRAPH_SOURCE_CHANGED", "发布快照或来源已变化，请刷新资料后重新摘录。")
    frozen, member = entry
    if request.chunk_sha256 != frozen.sha256 or chunk.sha256 != frozen.sha256 or chunk.text != frozen.text or sha256(chunk.text.encode("utf-8")).hexdigest() != frozen.sha256:
        raise ServiceError(409, "GRAPH_SOURCE_INVALID", "资料正文或哈希不一致，不能保存关系。")
    start, end = request.quote_start, request.quote_end
    if not 0 <= start < end <= len(chunk.text) or not 8 <= end - start <= 1200:
        raise ServiceError(422, "GRAPH_QUOTE_INVALID", "原文范围必须为切片内连续的 8 至 1200 个字符。")
    quote = chunk.text[start:end]
    if request.subject == request.object or request.subject not in quote or request.object not in quote:
        raise ServiceError(422, "GRAPH_ENTITY_NOT_IN_QUOTE", "两个不同实体的名称必须逐字出现在所选原文中。")
    spans = []
    for span in chunk.page_spans:
        lo, hi = max(start, span["chunk_start"]), min(end, span["chunk_end"])
        if lo < hi:
            spans.append({"page": span["page"], "char_start": span["char_start"] + lo - span["chunk_start"],
                          "char_end": span["char_start"] + hi - span["chunk_start"]})
    citation = {"evidence_id": "graph", "quote": quote, "chunk_id": str(chunk.id), "source_id": str(chunk.source_id),
        "version_id": str(chunk.document_version), "parse_id": str(chunk.parse_id), "document_version": member["version_number"],
        "source_metadata": frozen.source_metadata, "section": chunk.section, "page_spans": spans,
        "char_start": chunk.char_start + start, "char_end": chunk.char_start + end}
    if not spans:
        raise ServiceError(409, "GRAPH_SOURCE_LOCATION_INVALID", "原文没有已保存的页面映射。")
    checked_citation_source(session, citation)
    basis_hash = digest({"citation": citation, "source_basis": member, "subject": request.subject,
        "subject_kind": request.subject_kind, "object": request.object, "object_kind": request.object_kind,
        "predicate": request.predicate})
    row = session.scalar(select(GraphRelation).where(GraphRelation.fingerprint == basis_hash))
    deduplicated = row is not None
    if row is None:
        if session.scalar(select(func.count()).select_from(GraphRelation)) >= MAX_RELATIONS:
            raise ServiceError(422, "GRAPH_LIMIT_REACHED", "本轮图谱已达到一千条关系上限。")
        subject = create_entity(session, chunk.source_id, request.subject, request.subject_kind)
        target = create_entity(session, chunk.source_id, request.object, request.object_kind)
        row = GraphRelation(subject_id=subject.id, object_id=target.id, predicate=request.predicate,
            snapshot_id=snapshot.id, chunk_id=chunk.id, chunk_sha256=frozen.sha256, source_basis=member,
            citation=citation, fingerprint=basis_hash, creator_id=principal.user.id, note=request.note)
        session.add(row); session.flush()
    result = {"relation_id": str(row.id), "status": row.status, "revision": row.revision, "deduplicated": deduplicated}
    append_action(session, principal, row, request, request_id, fingerprint, "create", result)
    return {**result, "replayed": False}


def review(session, principal, relation_id, request, request_id):
    fingerprint = digest({"kind": "review", "relation_id": str(relation_id), "request": request.model_dump(mode="json")})
    mutation_lock(session, principal)
    previous = replay(session, principal, request.idempotency_key, fingerprint)
    if previous:
        return previous
    row = get_relation(session, relation_id)
    lock_source(session, principal, UUID(row.citation["version_id"]))
    row = get_relation(session, relation_id, lock=True)
    if row.revision != request.expected_revision:
        raise ServiceError(409, "GRAPH_REVISION_CONFLICT", "关系已被其他操作修改，请刷新后重新核对。")
    if not (row.status == "pending" and request.decision in ("approved", "rejected") or row.status == "approved" and request.decision == "withdrawn"):
        raise ServiceError(409, "GRAPH_REVIEW_TERMINAL", "该状态不能执行所选审核操作，历史决定不能改写。")
    if request.decision == "approved":
        if not request.evidence_checked or not request.direction_checked:
            raise ServiceError(422, "GRAPH_REVIEW_INCOMPLETE", "批准前必须核对原文支持和实体方向。")
        _, allowed = current_sources(session)
        if not available(row, allowed):
            raise ServiceError(409, "GRAPH_SOURCE_CHANGED", "原候选来源已失效或依据已变化，不能批准。")
        checked_citation_source(session, row.citation)
    row.status = request.decision; row.revision += 1
    result = {"relation_id": str(row.id), "status": row.status, "revision": row.revision}
    append_action(session, principal, row, request, request_id, fingerprint, "review", result)
    return {**result, "replayed": False}
