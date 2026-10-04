"""单轮问答配置独立于索引配置，不改变已发布的向量空间。"""

import json
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from machineguard.paths import get_project_root
from .index_config import digest


class QAConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_version: Literal["machineguard.qa.v1"] = Field(alias="schema")
    rerank_model: Literal["qwen3.7-text-rerank"]
    answer_model: Literal["qwen3.8-max-0902"]
    rerank_endpoint: Literal["https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"]
    chat_endpoint: Literal["https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"]
    key_env: Literal["DASHSCOPE_API_KEY"]
    recall_limit: Literal[20]
    evidence_limit: Literal[5]
    min_rerank_score: float = Field(ge=0, le=1, allow_inf_nan=False)
    request_seconds: int = Field(ge=10, le=60)
    timeout_seconds: int = Field(ge=240, le=600)
    max_input_tokens: int = Field(ge=1000, le=12000)
    max_output_tokens: int = Field(ge=512, le=2000)
    prompt_version: Literal["grounded-claims-quotes-v1", "grounded-claims-quotes-v2"]

    def frozen(self):
        value = self.model_dump(mode="json", by_alias=True)
        return value, digest(value)


def load_qa_configuration():
    return QAConfig.model_validate(json.loads((get_project_root() / "configs/qa.json").read_text(encoding="utf-8")))
