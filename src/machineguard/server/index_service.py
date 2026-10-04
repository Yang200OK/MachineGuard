"""审核版本生成一致知识快照，发布和任务终态在同一事务内提交。"""

from datetime import timedelta
from hashlib import sha256
from uuid import UUID, uuid4

from rank_bm25 import BM25Okapi
from sqlalchemy import func, select, text

from .auth_service import ServiceError, audit
from .document_models import Document, DocumentVersion
from .document_service import authorize
from .index_config import IndexConfig, digest, load_index_configuration
from .index_models import KnowledgeCatalog, KnowledgePublishRequest, KnowledgeSnapshot, SnapshotChunk
from .index_provider import embed, tokenize, validate_vector
from .job_models import Job
from .job_service import db_now, finish_job
from .knowledge_service import effective_metadata, review_state
from .parse_models import DocumentChunk, DocumentParse


def member_for(session, version, parse_id):
    doc = session.get(Document, version.document_id)
    if doc.archived_at is not None:
        raise ServiceError(409, "KNOWLEDGE_SOURCE_ARCHIVED", "资料已归档，不能作为新回答的有效来源。")
    parsed = session.get(DocumentParse, parse_id)
    if parsed is None or parsed.version_id != version.id or version.status != "uploaded" or not version.is_active:
        raise ServiceError(409, "KNOWLEDGE_SOURCE_UNAVAILABLE", "版本已停用、原文未确认或解析不属于此版本。")
    state = review_state(session, parsed)
    if state["review_status"] != "approved":
        raise ServiceError(409, "KNOWLEDGE_REVIEW_REQUIRED", "只有当前来源和解析结果的有效批准才可建立索引。")
    metadata, revision = effective_metadata(session, version)
    return {"document_id": str(version.document_id), "version_id": str(version.id), "version_number": version.number,
        "parse_id": str(parsed.id), "review_id": state["review"]["action_id"], "metadata_revision": revision,
        "source_metadata": metadata, "source_sha256": version.sha256, "result_sha256": parsed.result_sha256,
        "chunk_count": parsed.chunk_count}


def member_valid(session, member):
    version = session.get(DocumentVersion, UUID(member["version_id"]))
    try:
        return version is not None and member_for(session, version, UUID(member["parse_id"])) == member
    except ServiceError:
        # 此处是显式来源过滤，禁止把失效内容当作新查询的有效证据。
        return False


def snapshot_view(session, row):
    job = session.get(Job, row.job_id)
    current = session.get(KnowledgeCatalog, 1)
    return {"snapshot_id": str(row.id), "base_snapshot_id": str(row.base_id) if row.base_id else None,
        "job_id": str(row.job_id), "job_deleted": job.deleted_at is not None, "owner_id": str(job.owner_id), "status": job.status, "stage": job.stage, "progress": job.progress,
        "created_at": row.created_at.isoformat(), "published_at": row.published_at.isoformat() if row.published_at else None,
        "is_current": current.current_id == row.id, "model": row.config_snapshot["embedding_model"],
        "dimension": row.config_snapshot["dimension"], "config_sha256": row.config_sha256,
        "manifest_sha256": row.manifest_sha256, "chunk_count": row.chunk_count, "members": [{**m, "available": member_valid(session, m)} for m in row.members],
        "usage": row.usage, "error": {"code": job.error_code, "message": job.error_message} if job.error_code else None}


def publication_state(session, version_id, parse_id=None):
    current = session.get(KnowledgeCatalog, 1)
    if current is None or current.current_id is None:
        return "unpublished"
    snapshot = session.get(KnowledgeSnapshot, current.current_id)
    member = next((m for m in snapshot.members if m["version_id"] == str(version_id) and
                   (parse_id is None or m["parse_id"] == str(parse_id))), None)
    if member is None:
        return "unpublished"
    return "published" if member_valid(session, member) else "withdrawn"


def submit_publication(session, principal, version_id, request, worker_config, request_id):
    authorize(session, principal)
    fingerprint = digest({"version_id": str(version_id), "request": request.model_dump(mode="json")})
    owner_lock = int.from_bytes(sha256(f"jobs:{principal.user.id}".encode()).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": owner_lock})
    authorize(session, principal)
    receipt = session.scalar(select(KnowledgePublishRequest).where(KnowledgePublishRequest.owner_id == principal.user.id,
        KnowledgePublishRequest.idempotency_key == request.idempotency_key))
    if receipt:
        if receipt.request_fingerprint != fingerprint:
            raise ServiceError(409, "KNOWLEDGE_IDEMPOTENCY_CONFLICT", "发布请求键已绑定另一组版本、解析或修订参数。")
        return {**snapshot_view(session, session.get(KnowledgeSnapshot, receipt.snapshot_id)), "replayed": True}
    catalog = session.scalar(select(KnowledgeCatalog).where(KnowledgeCatalog.id == 1).with_for_update())
    version = session.scalar(select(DocumentVersion).where(DocumentVersion.id == version_id).with_for_update())
    authorize(session, principal)
    if version is None:
        raise ServiceError(404, "DOCUMENT_VERSION_NOT_FOUND", "文档版本不存在。")
    if version.management_revision != request.expected_revision:
        raise ServiceError(409, "KNOWLEDGE_REVISION_CONFLICT", "版本已修改，请刷新审核依据后再发布。")
    target = member_for(session, version, request.parse_id)
    config = load_index_configuration(); config_snapshot, config_sha256 = config.frozen()
    base = session.get(KnowledgeSnapshot, catalog.current_id) if catalog.current_id else None
    members = []
    if base:
        # 模型或配置变化时要求全量重建，不允许复制不同向量空间。
        if base.config_sha256 != config_sha256:
            raise ServiceError(409, "KNOWLEDGE_REBUILD_REQUIRED", "索引配置已变化，需要全量重建；不能混用现有快照向量。")
        for previous in base.members:
            if previous["document_id"] != target["document_id"] and member_valid(session, previous):
                members.append(previous)
    members.append(target); members.sort(key=lambda x: x["document_id"])
    if sum(m["chunk_count"] for m in members) > config.max_chunks:
        raise ServiceError(422, "KNOWLEDGE_INDEX_LIMIT", "本轮精确索引的切片总量已超过配置上限。")
    publication_hash = digest({"config_sha256": config_sha256, "members": members})
    row = session.scalar(select(KnowledgeSnapshot).join(Job, Job.id == KnowledgeSnapshot.job_id)
        .where(KnowledgeSnapshot.fingerprint == publication_hash, Job.status.in_(["queued", "running", "succeeded"]))
        .order_by(KnowledgeSnapshot.created_at.desc()).limit(1))
    # 已发布且仍为当前快照才能去重；历史快照不得意外覆盖当前发布指针。
    if row and row.published_at is not None and catalog.current_id != row.id:
        row = None
    if row is None:
        pending = session.scalar(select(func.count()).select_from(Job).where(Job.owner_id == principal.user.id,
            Job.status.in_(["queued", "running"])))
        if pending >= worker_config.max_pending_per_owner:
            raise ServiceError(429, "JOB_LIMIT_REACHED", "待处理任务已达到上限。")
        now = db_now(session); snapshot_id, job_id = uuid4(), uuid4()
        job = Job(id=job_id, owner_id=principal.user.id, kind="knowledge_index", request_id=request_id,
            payload={"snapshot_id": str(snapshot_id)}, idempotency_key="knowledge:" + str(request.idempotency_key),
            request_fingerprint=fingerprint, created_at=now, deadline_at=now + timedelta(seconds=config.timeout_seconds),
            max_attempts=1)
        session.add(job); session.flush()
        row = KnowledgeSnapshot(id=snapshot_id, job_id=job_id, base_id=catalog.current_id, created_by=principal.user.id,
            config_snapshot=config_snapshot, config_sha256=config_sha256, members=members,
            fingerprint=publication_hash, created_at=now)
        session.add(row); session.flush()
        audit(session, principal.user.id, "knowledge.index_submitted", "knowledge_snapshot", row.id, request_id,
            {"version_id": str(version.id), "parse_id": str(request.parse_id), "config_sha256": config_sha256})
    session.add(KnowledgePublishRequest(owner_id=principal.user.id, idempotency_key=request.idempotency_key,
        snapshot_id=row.id, request_fingerprint=fingerprint))
    return {**snapshot_view(session, row), "replayed": False}


def finish_publication(session, lease, result):
    catalog = session.scalar(select(KnowledgeCatalog).where(KnowledgeCatalog.id == 1).with_for_update())
    row = session.get(KnowledgeSnapshot, UUID(lease.payload["snapshot_id"]))
    if row is None or row.job_id != lease.job_id or row.published_at is not None:
        raise ServiceError(409, "KNOWLEDGE_TARGET_INVALID", "发布目标与任务不一致或已提交。")
    if catalog.current_id != row.base_id:
        raise ServiceError(409, "KNOWLEDGE_BASE_CHANGED", "当前知识快照已更新；本任务未发布，请重新核对后显式提交。")
    for member in sorted(row.members, key=lambda x: x["version_id"]):
        version = session.scalar(select(DocumentVersion).where(DocumentVersion.id == UUID(member["version_id"])).with_for_update())
        if version is None or member_for(session, version, UUID(member["parse_id"])) != member:
            raise ServiceError(409, "KNOWLEDGE_SOURCE_CHANGED", "索引期间来源、启用状态或批准依据变化；未发布半成品。")
    entries = result["entries"]
    if len(entries) != sum(m["chunk_count"] for m in row.members) or len({e["chunk_id"] for e in entries}) != len(entries):
        raise ServiceError(503, "KNOWLEDGE_INDEX_INVALID", "索引切片总量或唯一身份不符合快照。")
    manifest = digest({"fingerprint": row.fingerprint, "entries": entries})
    summary = {"snapshot_id": str(row.id), "chunk_count": len(entries), "manifest_sha256": manifest,
        "model": row.config_snapshot["embedding_model"], "dimension": row.config_snapshot["dimension"], "knowledge_status": "published"}
    # 先取得有效租约与取消检查，再在同一事务保存全部索引和发布指针。
    if not finish_job(session, lease, result=summary):
        return False
    for item in entries:
        vector = validate_vector(item["embedding"], 1024)
        session.add(SnapshotChunk(snapshot_id=row.id, chunk_id=UUID(item["chunk_id"]),
            version_id=UUID(item["version_id"]), text=item["text"], sha256=item["sha256"],
            source_metadata=item["source_metadata"], location=item["location"], tokens=item["tokens"], embedding=vector))
    row.published_at = db_now(session); row.chunk_count = len(entries); row.manifest_sha256 = manifest; row.usage = result["usage"]
    catalog.current_id = row.id; catalog.revision += 1
    audit(session, row.created_by, "knowledge.published", "knowledge_snapshot", row.id,
        session.get(Job, lease.job_id).request_id, summary)
    return True


def list_snapshots(session, principal, offset, limit):
    authorize(session, principal)
    rows = session.scalars(select(KnowledgeSnapshot).order_by(KnowledgeSnapshot.created_at.desc(), KnowledgeSnapshot.id.desc())
        .offset(offset).limit(limit)).all()
    # 发布指针独立返回，历史任务翻页不会让当前发布状态消失。
    current_id = session.get(KnowledgeCatalog, 1).current_id
    current = session.get(KnowledgeSnapshot, current_id) if current_id else None
    return {"current": snapshot_view(session, current) if current is not None else None,
        "items": [snapshot_view(session, row) for row in rows], "total": session.scalar(select(func.count()).select_from(KnowledgeSnapshot)),
        "offset": offset, "limit": limit}


def search_preview(session, principal, request, *, client=None):
    authorize(session, principal)
    snapshot_id = request.snapshot_id or session.get(KnowledgeCatalog, 1).current_id
    row = session.get(KnowledgeSnapshot, snapshot_id) if snapshot_id else None
    if row is None or row.published_at is None:
        raise ServiceError(409, "KNOWLEDGE_NOT_PUBLISHED", "尚无已发布的完整知识快照。")
    members = [m for m in row.members if member_valid(session, m) and
               (request.equipment_type is None or m["source_metadata"]["equipment_type"] == request.equipment_type)]
    versions = [UUID(m["version_id"]) for m in members]
    if not versions:
        return {"snapshot_id": str(row.id), "items": [], "query_usage": None, "reason": "当前范围没有有效审核来源。"}
    config = IndexConfig.model_validate(row.config_snapshot)
    vectors, usage = embed(config, [request.query], "query", client=client)
    authorize(session, principal)
    # 云请求期间可能停用或纠正来源，再次读取当前状态后执行过滤。
    session.expire_all()
    versions = [UUID(m["version_id"]) for m in members if member_valid(session, m)]
    corpus = session.scalars(select(SnapshotChunk).where(SnapshotChunk.snapshot_id == row.id, SnapshotChunk.version_id.in_(versions))
        .order_by(SnapshotChunk.chunk_id)).all()
    if not corpus:
        return {"snapshot_id": str(row.id), "items": [], "query_usage": usage, "reason": "查询期间来源已撤回。"}
    distance = SnapshotChunk.embedding.cosine_distance(vectors[0])
    semantic = session.execute(select(SnapshotChunk.chunk_id, distance.label("distance")).where(
        SnapshotChunk.snapshot_id == row.id, SnapshotChunk.version_id.in_(versions)).order_by(distance, SnapshotChunk.chunk_id).limit(20)).all()
    scores = BM25Okapi([x.tokens for x in corpus], k1=config.bm25_k1, b=config.bm25_b).get_scores(tokenize(request.query))
    query_tokens = set(tokenize(request.query))
    # BM25 小语料的分数可能为负，只按实际词项命中筛选，不能按 >0 误删候选。
    lexical = sorted([(x.chunk_id, float(score)) for x, score in zip(corpus, scores) if query_tokens.intersection(x.tokens)],
                     key=lambda x: (-x[1], str(x[0])))[:20]
    fused = {}; ranks = {}; raw = {}
    for channel, candidates in (("vector", semantic), ("bm25", lexical)):
        for rank, (chunk_id, score) in enumerate(candidates, 1):
            fused[chunk_id] = fused.get(chunk_id, 0) + 1 / (config.rrf_k + rank)
            ranks.setdefault(chunk_id, {})[channel] = rank
            raw.setdefault(chunk_id, {})["cosine_distance" if channel == "vector" else "bm25_score"] = float(score)
    by_id = {x.chunk_id: x for x in corpus}; items = []
    for chunk_id in sorted(fused, key=lambda x: (-fused[x], str(x)))[:request.limit]:
        chunk = by_id[chunk_id]
        items.append({"chunk_id": str(chunk_id), "version_id": str(chunk.version_id), "text": chunk.text,
            "source_metadata": chunk.source_metadata, **chunk.location, "rrf_score": fused[chunk_id],
            "ranks": ranks[chunk_id], **raw[chunk_id]})
    return {"snapshot_id": str(row.id), "items": items, "query_usage": usage,
        "limitation": "仅为双路召回及 RRF 预览，分数不是置信概率；尚未重排、证据门槛或生成回答。"}
