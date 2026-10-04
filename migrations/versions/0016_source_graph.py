"""文档实体、来源关系与追加审核记录。"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0016_source_graph"
down_revision = "0015_report_delivery"
branch_labels = depends_on = None


def upgrade():
    op.create_table("graph_entities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("document_id", sa.Uuid(), sa.ForeignKey("documents.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("name", sa.String(120), nullable=False), sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("document_id", "name", name="uq_graph_entity_name"),
        sa.CheckConstraint("length(name) > 0 AND length(kind) > 0", name="ck_graph_entity_name"))
    op.create_index("ix_graph_entities_document_id", "graph_entities", ["document_id"])
    op.create_table("graph_relations",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("subject_id", sa.Uuid(), sa.ForeignKey("graph_entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("object_id", sa.Uuid(), sa.ForeignKey("graph_entities.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("predicate", sa.String(80), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), sa.ForeignKey("knowledge_snapshots.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("chunk_id", sa.Uuid(), sa.ForeignKey("chunks.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("chunk_sha256", sa.String(64), nullable=False),
        sa.Column("source_basis", JSONB(), nullable=False), sa.Column("citation", JSONB(), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False, unique=True),
        sa.Column("creator_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("note", sa.Text(), nullable=False), sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("status IN ('pending','approved','rejected','withdrawn')", name="ck_graph_relation_status"),
        sa.CheckConstraint("revision >= 1 AND subject_id <> object_id", name="ck_graph_relation_revision"),
        sa.CheckConstraint("length(predicate) > 0 AND jsonb_typeof(citation) = 'object' AND jsonb_typeof(source_basis) = 'object'", name="ck_graph_relation_basis"))
    op.create_index("ix_graph_relations_chunk_id", "graph_relations", ["chunk_id"])
    op.create_table("graph_actions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("relation_id", sa.Uuid(), sa.ForeignKey("graph_relations.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("actor_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="RESTRICT"), nullable=False),
        sa.Column("idempotency_key", sa.Uuid(), nullable=False), sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(64), nullable=False), sa.Column("kind", sa.String(8), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False), sa.Column("payload", JSONB(), nullable=False),
        sa.Column("result", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("actor_id", "idempotency_key", name="uq_graph_action_key"),
        sa.CheckConstraint("kind IN ('create','review') AND revision >= 1", name="ck_graph_action_kind"))
    op.create_index("ix_graph_actions_relation_id", "graph_actions", ["relation_id"])
    # 运行账号不能改写原实体、原候选与历史动作，只能更新审核状态及修订。
    op.execute("GRANT SELECT, INSERT ON graph_entities, graph_relations, graph_actions TO machineguard_app")
    op.execute("REVOKE UPDATE ON graph_entities, graph_relations, graph_actions FROM machineguard_app")
    op.execute("GRANT UPDATE (status, revision) ON graph_relations TO machineguard_app")
    op.execute("REVOKE DELETE, TRUNCATE ON graph_entities, graph_relations, graph_actions FROM machineguard_app")


def downgrade():
    op.execute("DO $$ BEGIN IF EXISTS (SELECT 1 FROM graph_relations) THEN RAISE EXCEPTION '存在来源图谱记录，不能降级'; END IF; END $$")
    for table in ("graph_actions", "graph_relations", "graph_entities"):
        op.drop_table(table)
