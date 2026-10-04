"""解析配置固定到任务快照，Worker 不以更新配置替换旧任务参数。"""

from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from machineguard.server.auth_service import ServiceError


class ParsingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    schema_name: Literal["machineguard.parsing.v1"] = Field(alias="schema")
    parser_version: Literal["1.0.0"]
    tokenizer: Literal["o200k_base"]
    chunk_tokens: int = Field(ge=400, le=700)
    overlap_tokens: int = Field(ge=60, le=100)
    max_pdf_pages: int = Field(ge=1, le=50)
    max_visual_pages: int = Field(ge=1, le=6)
    max_text_chars: int = Field(ge=1000, le=300000)
    max_chunks: int = Field(ge=1, le=1500)
    timeout_seconds: int = Field(ge=120, le=600)
    vision_model: Literal["qwen3.8-max-0902"]
    vision_endpoint: str
    vision_key_env: Literal["DASHSCOPE_API_KEY"]
    vision_request_seconds: int = Field(ge=10, le=60)
    vision_max_tokens: int = Field(ge=1000, le=6000)
    render_dpi: int = Field(ge=72, le=150)
    render_max_side: int = Field(ge=800, le=1800)

    def frozen(self):
        body = self.model_dump(by_alias=True)
        return body, sha256(json.dumps(body, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_parsing_configuration():
    path = Path(__file__).resolve().parents[3] / "configs" / "parsing.json"
    try:
        config = ParsingConfig.model_validate_json(path.read_text(encoding="utf-8"))
        # 明确允许的百炼北京地址；管理员不能把任务配置变成任意 HTTP 转发器。
        if config.vision_endpoint != "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions":
            raise ValueError("视觉地址未登记。")
        return config
    except (OSError, UnicodeError, ValueError):
        raise ServiceError(503, "PARSE_CONFIG_INVALID", "解析配置不符合约束，请检查 configs/parsing.json。") from None
