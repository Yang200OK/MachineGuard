"""模型与运行预算固定在提交记录中，不自动选择替代模型。"""
import json
from typing import Literal
from pydantic import Field
from machineguard.paths import get_project_root
from .schemas import StrictRequest


class AgentConfig(StrictRequest):
    schema_version: Literal["machineguard.agent.v1"] = Field(alias="schema")
    answer_model: Literal["qwen3.8-max-0902"]
    chat_endpoint: Literal["https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"]
    key_env: Literal["DASHSCOPE_API_KEY"]
    request_seconds: int = Field(ge=10, le=60)
    timeout_seconds: int = Field(ge=360, le=900)
    max_input_tokens: int = Field(ge=8000, le=24000)
    max_output_tokens: int = Field(ge=512, le=2000)
    max_model_calls: Literal[6]
    max_tool_calls: Literal[8]
    max_pending_per_owner: int = Field(ge=1, le=3)
    max_guest_runs: int = Field(ge=1, le=10)


def load_agent_configuration():
    return AgentConfig.model_validate(json.loads((get_project_root() / "configs/agent.json").read_text("utf-8")))
