"""MCP 只加载项目配置，stdio 凭据由启动进程环境提供。"""

from urllib.parse import urlparse
import re
from pydantic import BaseModel, ConfigDict, Field, field_validator
from machineguard.paths import get_project_root


class McpConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    resource_url: str
    allowed_hosts: list[str] = Field(min_length=1)
    allowed_origins: list[str] = Field(min_length=1)
    max_calls_per_minute: int = Field(ge=5, le=100)
    max_knowledge_calls_per_minute: int = Field(ge=1, le=10)
    max_body_bytes: int = Field(ge=4096, le=65536)
    max_active_credentials: int = Field(ge=1, le=20)

    @field_validator("resource_url")
    @classmethod
    def exact_resource(cls, value):
        parsed = urlparse(value)
        if (parsed.path != "/mcp/" or parsed.query or parsed.fragment or parsed.username or parsed.password
                or parsed.scheme not in ("http", "https") or not parsed.hostname
                or (parsed.scheme == "http" and parsed.hostname not in ("127.0.0.1", "localhost"))):
            raise ValueError("MCP 地址必须为 HTTPS 或本机 HTTP，且以 /mcp/ 结尾。")
        return value

    @field_validator("allowed_hosts")
    @classmethod
    def concrete_hosts(cls, values):
        if len(set(values)) != len(values) or any(not re.fullmatch(r"[A-Za-z0-9.-]+(?::(?:[0-9]{1,5}|\*))?", value) for value in values):
            raise ValueError("MCP Host 必须是唯一的明确主机，可指定端口或端口通配符，不能通配主机。")
        return values

    @field_validator("allowed_origins")
    @classmethod
    def concrete_origins(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("MCP Origin 清单不能重复。")
        for value in values:
            parsed = urlparse(value)
            if (parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.path
                    or parsed.query or parsed.fragment or parsed.username or parsed.password or "*" in value):
                raise ValueError("MCP Origin 必须是明确的 HTTP/HTTPS 来源，不能含通配符或路径。")
        return values


def load_mcp_configuration():
    return McpConfig.model_validate_json((get_project_root() / "configs/mcp.json").read_text(encoding="utf-8"))
