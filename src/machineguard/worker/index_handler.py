"""固定审核来源建立向量，已发布且同配置的切片按快照复制。"""

from uuid import UUID
from hashlib import sha256

from sqlalchemy import select

from machineguard.server.auth_service import ServiceError
from machineguard.server.index_config import IndexConfig, digest
from machineguard.server.index_models import KnowledgeSnapshot, SnapshotChunk
from machineguard.server.index_provider import embed, tokenize
from machineguard.server.index_service import member_valid
from machineguard.server.parse_models import DocumentChunk


def knowledge_index(context, payload):
    context.stage("index_sources", 10)
    with context.database.sessions() as session:
        row = session.get(KnowledgeSnapshot, UUID(payload["snapshot_id"]))
        if row is None or row.job_id != context.lease.job_id or row.published_at is not None:
            raise ServiceError(409, "KNOWLEDGE_TARGET_INVALID", "任务索引目标不合法。")
        config = IndexConfig.model_validate(row.config_snapshot)
        if config.frozen()[1] != row.config_sha256 or digest({"config_sha256": row.config_sha256, "members": row.members}) != row.fingerprint:
            raise ServiceError(409, "KNOWLEDGE_CONFIG_CHANGED", "任务索引配置或成员指纹不一致。")
        base = session.get(KnowledgeSnapshot, row.base_id) if row.base_id else None
        if base and (base.published_at is None or base.config_sha256 != row.config_sha256):
            raise ServiceError(409, "KNOWLEDGE_BASE_INVALID", "基准快照未发布或向量空间不一致。")
        cached = {x.chunk_id: x for x in session.scalars(select(SnapshotChunk).where(SnapshotChunk.snapshot_id == row.base_id)).all()} if base else {}
        entries = []
        for member in row.members:
            if not member_valid(session, member):
                raise ServiceError(409, "KNOWLEDGE_SOURCE_CHANGED", "执行前审核来源已变化，请重新核对后发布。")
            chunks = session.scalars(select(DocumentChunk).where(DocumentChunk.parse_id == UUID(member["parse_id"]))
                .order_by(DocumentChunk.ordinal)).all()
            if len(chunks) != member["chunk_count"]:
                raise ServiceError(503, "KNOWLEDGE_CHUNKS_CHANGED", "解析切片数量与审核快照不一致。")
            for chunk in chunks:
                if not chunk.text.strip() or not tokenize(chunk.text):
                    raise ServiceError(422, "KNOWLEDGE_CHUNK_EMPTY", "切片没有可索引的文本或词项。")
                if sha256(chunk.text.encode()).hexdigest() != chunk.sha256:
                    raise ServiceError(503, "KNOWLEDGE_CHUNK_CHANGED", "切片正文哈希与保存记录不一致。")
                old = cached.get(chunk.id)
                reusable = old is not None and old.sha256 == chunk.sha256 and old.source_metadata == member["source_metadata"]
                entries.append({"chunk_id": str(chunk.id), "version_id": str(chunk.document_version), "text": chunk.text,
                    "sha256": chunk.sha256, "source_metadata": member["source_metadata"],
                    "location": {"source_id": str(chunk.source_id), "parse_id": str(chunk.parse_id),
                        "document_version": member["version_number"], "page": chunk.page, "page_spans": chunk.page_spans,
                        "section": chunk.section, "language": chunk.language, "char_start": chunk.char_start, "char_end": chunk.char_end},
                    "tokens": tokenize(chunk.text), "embedding": old.embedding.tolist() if reusable else None})
    usage = []
    missing = [x for x in entries if x["embedding"] is None]
    for start in range(0, len(missing), config.batch_size):
        context.stage("index_embedding", 20 + int(60 * start / max(1, len(missing))))
        batch = missing[start:start + config.batch_size]
        vectors, receipt = embed(config, [x["text"] for x in batch], "document")
        for item, vector in zip(batch, vectors):
            item["embedding"] = vector
        usage.append(receipt)
    context.stage("index_publish", 90)
    entries.sort(key=lambda x: x["chunk_id"])
    return {"entries": entries, "usage": usage}
