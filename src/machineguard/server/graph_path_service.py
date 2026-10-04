"""有界有向路径查询；每条边保留审核修订和固定原文，不推导新关系。"""

from uuid import UUID
from hashlib import sha256

from pydantic import Field, field_validator
from sqlalchemy import select

from .auth_service import ServiceError
from .document_models import Document, DocumentVersion
from .document_service import authorize
from .graph_models import GraphEntity, GraphRelation
from . import graph_service as graph
from .index_models import KnowledgeCatalog, KnowledgeSnapshot, SnapshotChunk
from .parse_models import DocumentChunk, DocumentParse
from .qa_source import checked_citation_source
from .schemas import StrictRequest

UUID_PATTERN = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"


class PathArguments(StrictRequest):
    subject: str = Field(min_length=1, max_length=120)
    target: str | None = Field(default=None, min_length=1, max_length=120)
    document_id: str | None = Field(default=None, pattern=UUID_PATTERN)
    max_hops: int = Field(default=2, ge=1, le=2)
    limit: int = Field(default=5, ge=1, le=10)

    @field_validator("subject", "target")
    @classmethod
    def meaningful(cls, value):
        if value is None:
            return value
        value = value.strip()
        if not value or any(ord(c) < 32 for c in value):
            raise ValueError("实体名称不能为空或包含控制字符。")
        return value


LIMITATION = "路径只表示逐条审核过的有向关系，不证明因果、现场故障或路径两端存在新的直接关系。不同文档的同名实体不会自动合并。"


def locked_sources(session, principal, snapshot_id=None, manifest=None):
    """目录、文档、版本、解析按固定顺序加共享锁，阻止来源撤回与提交竞态。"""
    authorize(session, principal)
    catalog = session.scalar(select(KnowledgeCatalog).where(KnowledgeCatalog.id == 1)
        .with_for_update(read=True).execution_options(populate_existing=True))
    selected = snapshot_id or (catalog.current_id if catalog else None)
    snapshot = session.get(KnowledgeSnapshot, selected) if selected else None
    if snapshot is None or snapshot.published_at is None or (manifest and snapshot.manifest_sha256 != manifest):
        raise ServiceError(409, "GRAPH_SNAPSHOT_UNAVAILABLE", "固定知识快照未发布或身份已改变。")
    current = session.get(KnowledgeSnapshot, catalog.current_id) if catalog and catalog.current_id else None
    members = [*snapshot.members, *(current.members if current else [])]
    for model, field, key in ((Document, Document.id, "document_id"),
                              (DocumentVersion, DocumentVersion.id, "version_id"),
                              (DocumentParse, DocumentParse.id, "parse_id")):
        identities = sorted({UUID(m[key]) for m in members})
        list(session.scalars(select(model).where(field.in_(identities)).order_by(field)
            .with_for_update(read=True).execution_options(populate_existing=True)))
    authorize(session, principal)
    _, allowed = graph.current_sources(session)
    frozen_members = {m["version_id"]: m for m in snapshot.members}
    frozen_chunks = {row.chunk_id: row for row in session.scalars(select(SnapshotChunk)
        .where(SnapshotChunk.snapshot_id == snapshot.id))}
    # 固定快照和当前有效来源取交集；旧来源、跨快照新内容不能混入此运行。
    allowed = {key: entry for key, entry in allowed.items() if key in frozen_chunks
        and frozen_chunks[key].sha256 == entry[0].sha256
        and frozen_members.get(str(entry[0].version_id)) == entry[1]}
    return snapshot, allowed


def search_paths(session, principal, request, *, snapshot_id=None, manifest=None):
    snapshot, allowed = locked_sources(session, principal, snapshot_id, manifest)
    rows = session.scalars(select(GraphRelation).where(GraphRelation.status == "approved")
        .order_by(GraphRelation.id).limit(graph.MAX_RELATIONS + 1)
        .execution_options(populate_existing=True)).all()
    if len(rows) > graph.MAX_RELATIONS:
        raise ServiceError(422, "GRAPH_LIMIT_REACHED", "路径查询超过一千条关系的本轮规模限制。")
    rows = [row for row in rows if graph.available(row, allowed)]
    entities = {key: session.get(GraphEntity, key) for row in rows for key in (row.subject_id, row.object_id)}
    if request.document_id:
        entities = {key: entity for key, entity in entities.items() if str(entity.document_id) == request.document_id.lower()}
        rows = [row for row in rows if row.subject_id in entities and row.object_id in entities]
    base = {"query": request.model_dump(mode="json"), "snapshot_id": str(snapshot.id),
        "snapshot_manifest": snapshot.manifest_sha256, "paths": [], "candidates": [],
        "truncated": False, "limitation": LIMITATION}
    starts = [entity for entity in entities.values() if entity.name == request.subject]
    targets = [entity for entity in entities.values() if entity.name == request.target] if request.target else []
    ambiguous = [("subject", starts), ("target", targets)]
    for field, matches in ambiguous:
        if len(matches) > 1:
            candidates = [{"field": field, **graph.entity_view(session, entity)} for entity in sorted(matches, key=lambda e: str(e.id))]
            return {**base, "status": "clarification", "reason": "实体名称对应多个文档，请指定文档范围后查询。", "candidates": candidates}
    if not starts or (request.target and not targets):
        return {**base, "status": "no_path", "reason": "指定实体没有当前有效的已批准关系；未采用待审核或历史关系。"}
    start = starts[0].id; target = targets[0].id if targets else None
    outgoing = {}
    for row in rows:
        outgoing.setdefault(row.subject_id, []).append(row)
    found = []

    def walk(node, visited, edges):
        for row in outgoing.get(node, []):
            if row.object_id in visited:
                continue
            chain = [*edges, row]
            if target is None or row.object_id == target:
                found.append(chain)
                if len(found) > request.limit:
                    return True
            if len(chain) < request.max_hops and row.object_id != target:
                if walk(row.object_id, {*visited, row.object_id}, chain):
                    return True
        return False

    walk(start, {start}, [])
    paths = []
    for chain in found[:request.limit]:
        edges = []
        for row in chain:
            # 哈希、原文范围和页映射在返回前逐条核对，不仅依赖 approved 标记。
            checked_citation_source(session, row.citation)
            frozen = session.get(SnapshotChunk, (snapshot.id, row.chunk_id))
            current = allowed[row.chunk_id][0]
            original = session.get(DocumentChunk, row.chunk_id)
            expected_location = {"source_id": str(original.source_id), "parse_id": str(original.parse_id),
                "document_version": row.source_basis["version_number"], "page": original.page,
                "page_spans": original.page_spans, "section": original.section, "language": original.language,
                "char_start": original.char_start, "char_end": original.char_end}
            if (frozen.text != current.text or current.text != original.text
                    or sha256(original.text.encode("utf-8")).hexdigest() != row.chunk_sha256
                    or frozen.source_metadata != row.source_basis["source_metadata"]
                    or current.source_metadata != frozen.source_metadata
                    or frozen.location != expected_location or current.location != expected_location
                    or str(entities[row.subject_id].document_id) != row.citation["source_id"]
                    or str(entities[row.object_id].document_id) != row.citation["source_id"]):
                raise ServiceError(409, "GRAPH_SOURCE_INVALID", "路径原文、命名空间或固定发布位置不一致。")
            edges.append({"relation_id": str(row.id), "revision": row.revision,
                "subject": graph.entity_view(session, entities[row.subject_id]),
                "object": graph.entity_view(session, entities[row.object_id]), "predicate": row.predicate,
                "citation": row.citation, "chunk_sha256": row.chunk_sha256})
        paths.append({"hops": len(edges), "edges": edges})
    return {**base, "paths": paths, "truncated": len(found) > request.limit,
        "status": "paths" if paths else "no_path",
        "reason": None if paths else "在指定方向和跳数上限内，没有已批准且来源有效的路径。"}


def validate_result(session, principal, scope, result):
    """终态提交再次按同一参数计算，拒绝篡改、撤回或来源改变后的结果。"""
    try:
        if scope.snapshot_id is None or not scope.snapshot_manifest:
            raise ValueError()
        request = PathArguments.model_validate(result["query"])
        checked = search_paths(session, principal, request, snapshot_id=scope.snapshot_id, manifest=scope.snapshot_manifest)
    except (KeyError, TypeError, ValueError):
        raise ServiceError(409, "GRAPH_PATH_INVALID", "路径结果或查询参数不符合固定契约。") from None
    if result != checked:
        raise ServiceError(409, "GRAPH_PATH_CHANGED", "路径、审核修订或来源在提交前已改变，请重新查询。")
