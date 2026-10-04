"""独立 Worker 配置，参数错误直接阻止启动。"""

import json

from pydantic import BaseModel, ConfigDict, Field, model_validator

from machineguard.paths import get_project_root


class WorkerConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    lease_seconds: int = Field(ge=10, le=600)
    heartbeat_seconds: int = Field(ge=1, le=60)
    poll_seconds: float = Field(gt=0, le=30, allow_inf_nan=False)
    max_attempts: int = Field(ge=1, le=10)
    timeout_seconds: int = Field(ge=30, le=3600)
    max_pending_per_owner: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def bounded_heartbeat(self):
        if self.heartbeat_seconds * 3 > self.lease_seconds:
            raise ValueError("租约长度至少为心跳间隔的三倍。")
        if self.timeout_seconds < self.lease_seconds:
            raise ValueError("任务总时限不能短于租约。")
        return self


def load_worker_configuration() -> WorkerConfiguration:
    return WorkerConfiguration.model_validate(json.loads(
        (get_project_root() / "configs/worker.json").read_text(encoding="utf-8")))
