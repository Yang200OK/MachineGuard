"""百炼文本向量适配：严格验证索引、维度、有限值和非零向量。"""

import math
import os
import re

import httpx
import numpy as np

from .auth_service import ServiceError


def tokenize(value):
    # 保留 sensor_11、型号和数字；汉字连续片段采用单字与相邻双字固定规则。
    tokens = []
    for match in re.finditer(r"[a-zA-Z0-9_]+(?:[.\-][a-zA-Z0-9_]+)*|[\u3400-\u9fff]+", value.lower()):
        word = match.group()
        if '\u3400' <= word[0] <= '\u9fff':
            tokens.extend(word)
            tokens.extend(word[i:i + 2] for i in range(len(word) - 1))
        else:
            tokens.append(word)
    return tokens


def validate_vector(vector, dimension):
    if not isinstance(vector, list) or len(vector) != dimension or any(
            type(x) not in (int, float) or not math.isfinite(x) for x in vector) or sum(x * x for x in vector) <= 0:
        raise ServiceError(503, "EMBEDDING_OUTPUT_INVALID", "向量维度、数值或范数不符合索引约定。")
    with np.errstate(over="ignore", under="ignore"):
        normalized = np.asarray(vector, dtype=np.float32)
    if not np.isfinite(normalized).all() or not np.any(normalized):
        raise ServiceError(503, "EMBEDDING_OUTPUT_INVALID", "向量不能表示为数据库的有限非零单精度数值。")
    return normalized.tolist()


def embed(config, texts, text_type, *, client=None):
    if text_type not in ("query", "document") or not 1 <= len(texts) <= config.batch_size or any(not x.strip() for x in texts):
        raise ServiceError(422, "EMBEDDING_INPUT_INVALID", "向量请求类型、批量大小或文本不合法。")
    key = os.environ.get(config.key_env)
    if not key:
        raise ServiceError(503, "EMBEDDING_KEY_MISSING", "Worker/API 未继承 DASHSCOPE_API_KEY，请重新启动对应终端。")
    try:
        send = httpx.post if client is None else client.post
        response = send(config.embedding_endpoint, headers={"Authorization": "Bearer " + key},
            json={"model": config.embedding_model, "input": {"texts": texts},
                "parameters": {"dimension": config.dimension, "text_type": text_type, "output_type": "dense"}},
            timeout=httpx.Timeout(config.request_seconds, connect=10))
    except httpx.HTTPError:
        raise ServiceError(503, "EMBEDDING_CONNECTION_FAILED", "百炼向量请求连接失败或超时；未重试或替换模型。") from None
    try:
        data = response.json()
        if not response.is_success:
            # 仅保留官方错误码与请求编号，避免上游错误文本带出凭据或原文。
            code = str(data.get("code", "UNKNOWN"))
            request_id = str(data.get("request_id", "unknown"))
            code = re.sub(r"[^a-zA-Z0-9_.-]", "", code)[:60]
            request_id = re.sub(r"[^a-zA-Z0-9_.-]", "", request_id)[:80]
            raise ServiceError(503, "EMBEDDING_REQUEST_FAILED", f"百炼向量请求失败：HTTP {response.status_code}，{code}，请求 {request_id}。")
        values = data["output"]["embeddings"]
        if len(values) != len(texts) or sorted(x["text_index"] for x in values) != list(range(len(texts))):
            raise ValueError("向量条数或输入位置不一致。")
        vectors = [validate_vector(x["embedding"], config.dimension) for x in sorted(values, key=lambda x: x["text_index"])]
        usage = data.get("usage")
        if usage is not None and (not isinstance(usage, dict) or any(type(x) is not int or x < 0 for x in usage.values())):
            raise ValueError("用量格式不合法。")
        return vectors, {"request_id": data.get("request_id"), "text_type": text_type, "usage": usage}
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ServiceError(503, "EMBEDDING_OUTPUT_INVALID", "百炼向量响应结构、位置或用量不符合约定。") from None
