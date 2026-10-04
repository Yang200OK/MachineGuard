"""原生工具选择协议；限制消息、参数和输出，不接受模型推理过程。"""

import json
from .auth_service import ServiceError
from .qa_provider import post, receipt, tokens, reject_duplicate_keys


def parse_json(raw):
    if not isinstance(raw, str):
        raise ValueError("必须为 JSON 文本。")
    return json.loads(raw, object_pairs_hook=reject_duplicate_keys,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))


def chat(config, messages, tools=None, *, client):
    if tokens(json.dumps({"messages": messages, "tools": tools}, ensure_ascii=False)) > config.max_input_tokens:
        raise ServiceError(422, "AGENT_INPUT_BUDGET", "分析输入超过固定 token 上限。")
    body = {"model": config.answer_model, "messages": messages, "temperature": 0,
            "enable_thinking": False, "preserve_thinking": False, "max_tokens": config.max_output_tokens}
    if tools is None:
        body["response_format"] = {"type": "json_object"}
    else:
        body.update(tools=[{"type": x["type"], "function": x["function"]} for x in tools],
                    tool_choice="auto", parallel_tool_calls=False)
    value = post(config, config.chat_endpoint, body, "分析模型", client=client)
    try:
        choices = value["choices"]
        if len(choices) != 1:
            raise ValueError()
        choice = choices[0]; message = choice["message"]
        if choice["finish_reason"] == "tool_calls" and tools is not None:
            calls = message["tool_calls"]
            if not isinstance(calls, list) or not 1 <= len(calls) <= 5:
                raise ValueError()
            cleaned = []
            for call in calls:
                if call["type"] != "function" or not isinstance(call["id"], str) or not 1 <= len(call["id"]) <= 100:
                    raise ValueError()
                name = call["function"]["name"]; raw = call["function"]["arguments"]
                arguments = parse_json(raw)
                if not isinstance(name, str) or not isinstance(arguments, dict):
                    raise ValueError()
                cleaned.append({"id": call["id"], "type": "function", "function": {"name": name, "arguments": raw}})
            if len({x["id"] for x in cleaned}) != len(cleaned):
                raise ValueError()
            result = {"role": "assistant", "content": "", "tool_calls": cleaned}
        elif choice["finish_reason"] == "stop":
            result = parse_json(message["content"])
            if not isinstance(result, dict):
                raise ValueError()
        else:
            raise ValueError()
        return result, receipt(value, "agent", config.answer_model)
    except (KeyError, TypeError, ValueError):
        raise ServiceError(503, "AGENT_MODEL_PROTOCOL", "模型工具协议或 JSON 不合法，未执行替代方案。") from None
