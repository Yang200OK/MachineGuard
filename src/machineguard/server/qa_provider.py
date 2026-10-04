"""固定百炼重排和 JSON 生成接口，不重试、不替换模型。"""

import json
import math
import os
import re
import httpx
import tiktoken
from .auth_service import ServiceError


def post(config, endpoint, body, stage, *, client=None):
    key = os.environ.get(config.key_env)
    if not key:
        raise ServiceError(503, "QA_KEY_MISSING", "问答 Worker 未继承 DASHSCOPE_API_KEY。")
    try:
        # Agent 显式传入运行级连接池；已有问答保留原请求方式，均不重试。
        send = httpx.post if client is None else client.post
        response = send(endpoint, headers={"Authorization": "Bearer " + key}, json=body,
                              timeout=httpx.Timeout(config.request_seconds, connect=10))
    except httpx.HTTPError:
        raise ServiceError(503, "QA_CONNECTION_FAILED", f"{stage}连接失败或超时；未重复调用。") from None
    try:
        value = response.json()
        if not isinstance(value, dict):
            raise ValueError()
    except (ValueError, TypeError):
        raise ServiceError(503, "QA_RESPONSE_INVALID", f"{stage}未返回合法 JSON 对象。") from None
    if not response.is_success:
        code = re.sub(r"[^a-zA-Z0-9_.-]", "", str(value.get("code", "UNKNOWN")))[:60]
        request_id = re.sub(r"[^a-zA-Z0-9_.-]", "", str(value.get("request_id", "unknown")))[:80]
        raise ServiceError(503, "QA_REQUEST_FAILED", f"{stage}失败：HTTP {response.status_code}，{code}，请求 {request_id}。")
    return value


def receipt(value, stage, model):
    usage = value.get("usage")
    # 聊天用量可能包含 token 明细，保留合法计数，不把缺失用量写成零。
    def valid(item):
        return (type(item) is int and item >= 0) or (isinstance(item, dict) and all(valid(x) for x in item.values()))
    if usage is not None and (not isinstance(usage, dict) or not valid(usage)):
        raise ServiceError(503, "QA_RESPONSE_INVALID", "模型用量结构不符合约定。")
    return {"stage": stage, "model": model, "request_id": value.get("request_id", value.get("id")), "usage": usage}


def tokens(value):
    return len(tiktoken.get_encoding("o200k_base").encode(value, disallowed_special=()))


def rerank(config, query, passages, *, client=None):
    if not 1 <= len(passages) <= config.recall_limit:
        raise ServiceError(422, "QA_INPUT_INVALID", "重排候选数量不符合限额。")
    if sum(tokens(query) + tokens(x) for x in passages) > config.max_input_tokens:
        raise ServiceError(422, "QA_BUDGET_EXCEEDED", "重排输入超过本轮 token 预算。")
    value = post(config, config.rerank_endpoint, {"model": config.rerank_model,
        "input": {"query": query, "documents": passages}, "parameters": {"top_n": len(passages),
        "instruct": "Given a web search query, retrieve relevant passages that answer the query."}}, "证据重排", client=client)
    try:
        results = value["output"]["results"]
        if not isinstance(results, list) or len(results) != len(passages):
            raise ValueError()
        indices = [x["index"] for x in results]
        if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(passages))):
            raise ValueError()
        for item in results:
            score = item["relevance_score"]
            if type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError()
        ordered = sorted(results, key=lambda x: (-x["relevance_score"], x["index"]))
        return ordered, receipt(value, "rerank", config.rerank_model)
    except (KeyError, TypeError, ValueError):
        raise ServiceError(503, "QA_RERANK_INVALID", "重排索引、条数或分数不符合约定。") from None


def reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("重复 JSON 字段。")
        value[key] = item
    return value


def generate_json(config, system, content, stage, *, client=None):
    if tokens(system) + tokens(content) > config.max_input_tokens:
        raise ServiceError(422, "QA_BUDGET_EXCEEDED", "生成输入超过本轮 token 预算。")
    value = post(config, config.chat_endpoint, {"model": config.answer_model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": content}],
        "response_format": {"type": "json_object"}, "temperature": 0,
        "enable_thinking": False, "preserve_thinking": False, "max_tokens": config.max_output_tokens}, stage, client=client)
    try:
        choices = value["choices"]
        if len(choices) != 1 or choices[0]["finish_reason"] != "stop":
            raise ValueError()
        raw = choices[0]["message"]["content"]
        if not isinstance(raw, str):
            raise ValueError()
        result = json.loads(raw, object_pairs_hook=reject_duplicate_keys,
                            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(result, dict):
            raise ValueError()
        return result, receipt(value, stage, config.answer_model)
    except (KeyError, TypeError, ValueError):
        raise ServiceError(503, "QA_GENERATION_INVALID", f"{stage}结构不合法或输出被截断。") from None
