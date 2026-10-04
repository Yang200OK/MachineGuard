"""独立冻结会话预算及辅助模型，不改写已有单轮配置。"""

import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from machineguard.paths import get_project_root
from .index_config import digest


class ConversationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["machineguard.conversation.v1"] = Field(alias="schema")
    answer_model: Literal["qwen3.7-plus"]
    chat_endpoint: Literal["https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"]
    key_env: Literal["DASHSCOPE_API_KEY"]
    request_seconds: int = Field(ge=10, le=60)
    timeout_seconds: int = Field(ge=360, le=600)
    max_input_tokens: int = Field(ge=1000, le=8000)
    max_output_tokens: int = Field(ge=256, le=600)
    history_turns: int = Field(ge=1, le=3)
    max_context_tokens: int = Field(ge=100, le=2000)
    max_turns: int = Field(ge=1, le=20)
    prompt_version: Literal["restricted-reference-spans-v1", "restricted-reference-terms-v2"]

    def frozen(self):
        value = self.model_dump(mode="json", by_alias=True)
        return value, digest(value)


def load_conversation_configuration():
    return ConversationConfig.model_validate(json.loads(
        (get_project_root() / "configs/conversation.json").read_text(encoding="utf-8")))
