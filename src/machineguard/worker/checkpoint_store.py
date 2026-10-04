"""实现 LangGraph Checkpointer；所有写入在有效 Worker 租约锁内提交。"""

from hashlib import sha256
import json
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from langgraph.checkpoint.base import BaseCheckpointSaver, CheckpointTuple, WRITES_IDX_MAP, get_checkpoint_metadata
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from machineguard.server import agent_service as service, job_service
from machineguard.server.agent_models import AgentRun
from machineguard.server.auth_service import ServiceError
from machineguard.server.recovery_models import GraphCheckpoint, GraphWrite, AgentAction


RECOVERY_VERSION = "one-click-v1"


class PostgresCheckpointSaver(BaseCheckpointSaver):
    def __init__(self, execution):
        super().__init__(serde=JsonPlusSerializer(pickle_fallback=False))
        self.execution = execution
        self.database = execution.database
        self.run_id = execution.run_id

    def identity(self, config):
        values = config["configurable"]
        if values["thread_id"] != str(self.run_id) or values.get("checkpoint_ns", ""):
            raise ServiceError(409, "CHECKPOINT_SCOPE_INVALID", "图状态必须属于当前运行，不能切换身份或命名空间。")
        return values.get("checkpoint_id")

    def get_tuple(self, config):
        checkpoint_id = self.identity(config)
        with self.database.sessions() as session:
            query = select(GraphCheckpoint).where(GraphCheckpoint.run_id == self.run_id)
            query = query.where(GraphCheckpoint.checkpoint_id == checkpoint_id) if checkpoint_id else query.order_by(GraphCheckpoint.checkpoint_id.desc()).limit(1)
            row = session.scalar(query)
            if row is None:
                return None
            saved_config = {"configurable": {"thread_id": str(self.run_id), "checkpoint_ns": "", "checkpoint_id": row.checkpoint_id}}
            writes = session.scalars(select(GraphWrite).where(GraphWrite.run_id == self.run_id,
                GraphWrite.checkpoint_id == row.checkpoint_id).order_by(GraphWrite.task_id, GraphWrite.index)).all()
            return CheckpointTuple(config=saved_config, checkpoint=self.serde.loads_typed((row.value_type, row.value)),
                metadata=self.serde.loads_typed((row.metadata_type, row.metadata_value)),
                parent_config={"configurable": {**saved_config["configurable"], "checkpoint_id": row.parent_id}} if row.parent_id else None,
                pending_writes=[(w.task_id, w.channel, self.serde.loads_typed((w.value_type, w.value))) for w in writes])

    def list(self, config, *, filter=None, before=None, limit=None):
        if config is None:
            raise ServiceError(409, "CHECKPOINT_SCOPE_INVALID", "读取图历史必须指定当前运行。")
        self.identity(config)
        with self.database.sessions() as session:
            query = select(GraphCheckpoint.checkpoint_id).where(GraphCheckpoint.run_id == self.run_id)
            if before:
                query = query.where(GraphCheckpoint.checkpoint_id < self.identity(before))
            ids = session.scalars(query.order_by(GraphCheckpoint.checkpoint_id.desc())).all()
        count = 0
        for checkpoint_id in ids:
            item = self.get_tuple({"configurable": {"thread_id": str(self.run_id), "checkpoint_id": checkpoint_id}})
            if filter and any(item.metadata.get(k) != v for k, v in filter.items()):
                continue
            if limit is not None and count >= limit:
                break
            yield item
            count += 1

    def put(self, config, checkpoint, metadata, new_versions):
        parent = self.identity(config)
        value_type, value = self.serde.dumps_typed(checkpoint)
        metadata_type, metadata_value = self.serde.dumps_typed(get_checkpoint_metadata(config, metadata))
        with self.database.sessions.begin() as session:
            self.execution.guard(session, True)
            row = session.scalar(select(AgentRun).where(AgentRun.id == self.run_id).with_for_update())
            service.principal_for(session, row)
            session.execute(insert(GraphCheckpoint).values(run_id=self.run_id, checkpoint_id=checkpoint["id"],
                parent_id=parent, value_type=value_type, value=value, metadata_type=metadata_type,
                metadata_value=metadata_value).on_conflict_do_nothing())
            # 只更新到更晚的图快照，旧 Worker 无权写入。
            if row.checkpoint_id is None or checkpoint["id"] > row.checkpoint_id:
                row.checkpoint_id = checkpoint["id"]
        return {"configurable": {"thread_id": str(self.run_id), "checkpoint_ns": "", "checkpoint_id": checkpoint["id"]}}

    def put_writes(self, config, writes, task_id, task_path=""):
        checkpoint_id = self.identity(config)
        with self.database.sessions.begin() as session:
            self.execution.guard(session, True)
            service.principal_for(session, session.get(AgentRun, self.run_id))
            for index, (channel, value) in enumerate(writes):
                value_type, binary = self.serde.dumps_typed(value)
                mapped = WRITES_IDX_MAP.get(channel, index)
                statement = insert(GraphWrite).values(run_id=self.run_id, checkpoint_id=checkpoint_id,
                    task_id=task_id, index=mapped, channel=channel, value_type=value_type, value=binary)
                if mapped < 0:
                    statement = statement.on_conflict_do_update(index_elements=["run_id", "checkpoint_id", "task_id", "index"],
                        set_={"channel": channel, "value_type": value_type, "value": binary})
                else:
                    statement = statement.on_conflict_do_nothing()
                session.execute(statement)


class ActionJournal:
    def __init__(self, execution):
        self.execution = execution

    def begin(self, key, inputs):
        fingerprint = sha256(json.dumps(inputs, ensure_ascii=False, sort_keys=True, allow_nan=False).encode("utf-8")).hexdigest()
        with self.execution.database.sessions.begin() as session:
            self.execution.guard(session, True)
            run = session.scalar(select(AgentRun).where(AgentRun.id == self.execution.run_id).with_for_update())
            service.principal_for(session, run)
            row = session.get(AgentAction, (run.id, key))
            if row is not None:
                if row.input_sha256 != fingerprint:
                    raise ServiceError(409, "AGENT_ACTION_CHANGED", "恢复动作参数与保存时不一致。")
                if row.status != "completed":
                    raise ServiceError(409, "AGENT_ACTION_UNCERTAIN", "进程在动作执行中退出，无法确认结果；未重复调用，请核对轨迹后显式新建分析。")
                service.add_event(session, run, "action_reused", {"action_key": key})
                return row.result
            session.add(AgentAction(run_id=run.id, action_key=key, input_sha256=fingerprint, status="started"))
        return None

    def complete(self, session, key, result):
        self.execution.guard(session, True)
        run = session.scalar(select(AgentRun).where(AgentRun.id == self.execution.run_id).with_for_update())
        service.principal_for(session, run)
        row = session.get(AgentAction, (run.id, key))
        if row is None or row.status != "started":
            raise ServiceError(409, "AGENT_ACTION_INVALID", "动作保存状态不符合约定。")
        row.result = service.jsonable(result)
        row.status = "completed"; row.completed_at = job_service.db_now(session)
        service.add_event(session, run, "action_saved", {"action_key": key})

    def call(self, key, inputs, operation):
        saved = self.begin(key, inputs)
        if saved is not None:
            return saved
        result = operation()
        with self.execution.database.sessions.begin() as session:
            self.complete(session, key, result)
        return result
