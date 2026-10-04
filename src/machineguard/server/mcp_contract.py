"""MCP 复用原工具参数 Schema，新增明确的设备范围及预测请求键。"""

from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal
from uuid import UUID
from pydantic import Field, field_validator
from .schemas import StrictRequest
from .tool_runtime import TOOLS, TrendArguments, HistoryArguments
from .graph_path_service import PathArguments
from .index_routes import PublishRequest

ScopeName = Literal["list_engines", "get_engine_overview", "get_sensor_trend", "predict_rul", "get_prediction_history", "search_maintenance_knowledge", "search_evidence_paths", "read_report"]


class DeviceArguments(StrictRequest):
    engine_key: str = Field(min_length=1, max_length=96)
    end_cycle: int = Field(ge=1)


class BoundTrend(TrendArguments, DeviceArguments):
    pass


class BoundHistory(HistoryArguments, DeviceArguments):
    pass


class PredictionArguments(DeviceArguments):
    idempotency_key: UUID

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def uuid_key(cls, value):
        return PublishRequest.json_uuid(value)


class EngineListArguments(HistoryArguments):
    pass


class ReportArguments(StrictRequest):
    report_id: UUID

    @field_validator("report_id", mode="before")
    @classmethod
    def uuid_id(cls, value):
        return PublishRequest.json_uuid(value)


class CredentialRequest(StrictRequest):
    client_name: str = Field(min_length=1, max_length=64)
    scopes: list[ScopeName] = Field(min_length=1, max_length=8)
    expires_minutes: int = Field(default=60, ge=5, le=1440)
    idempotency_key: UUID

    @field_validator("client_name")
    @classmethod
    def clean_name(cls, value):
        value = value.strip()
        if not value or any(ord(char) < 32 for char in value):
            raise ValueError("客户端名称不能为空或含控制字符。")
        return value

    @field_validator("scopes")
    @classmethod
    def unique_scopes(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("能力不能重复。")
        return sorted(value)

    @field_validator("idempotency_key", mode="before")
    @classmethod
    def key_uuid(cls, value):
        return PublishRequest.json_uuid(value)


@dataclass(frozen=True)
class McpTool:
    name: str
    description: str
    arguments: type[StrictRequest]
    permissions: tuple[str, ...]
    writes: bool = False


_bound = {"get_engine_overview": DeviceArguments, "get_sensor_trend": BoundTrend,
          "predict_rul": PredictionArguments, "get_prediction_history": BoundHistory}
MCP_TOOLS = MappingProxyType({tool.name: tool for tool in [
    McpTool("list_engines", "分页查询当前身份可访问的设备，不返回未来观测。", EngineListArguments, ("devices:read",)),
    *[McpTool(name, item.description, _bound.get(name, item.arguments),
        (item.permission, "devices:read") if item.writes_prediction else (item.permission,), item.writes_prediction)
      for name, item in TOOLS.items()],
    McpTool("read_report", "读取本人或分配给当前审核人的正式报告，不能自动批准报告。", ReportArguments, ()),
]})


def has_tool_permission(principal, name):
    from .security import permissions
    granted = set(permissions([role.name for role in principal.user.roles]))
    if name == "read_report":
        return bool(granted & {"analysis:create", "reports:review"})
    return set(MCP_TOOLS[name].permissions).issubset(granted)
