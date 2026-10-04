"""索引配置固定到知识快照，模型与维度禁止混用。"""

from hashlib import sha256
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ..paths import get_project_root


class IndexConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    schema_name: Literal["machineguard.indexing.v1"] = Field(alias="schema")
    embedding_model: Literal["qwen3.7-text-embedding"]
    dimension: Literal[1024]
    embedding_endpoint: Literal["https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding"]
    key_env: Literal["DASHSCOPE_API_KEY"]
    batch_size: int = Field(ge=1, le=20)
    request_seconds: int = Field(ge=5, le=60)
    timeout_seconds: int = Field(ge=120, le=1800)
    max_chunks: int = Field(ge=1, le=1500)
    tokenizer_version: Literal["latin-identifiers-han-bigrams-v1"]
    bm25_k1: float = Field(ge=0.5, le=3)
    bm25_b: float = Field(ge=0, le=1)
    rrf_k: int = Field(ge=1, le=100)

    def frozen(self):
        value = self.model_dump(by_alias=True)
        return value, digest(value)


def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def load_index_configuration():
    return IndexConfig.model_validate_json((get_project_root() / "configs/indexing.json").read_text(encoding="utf-8"))
