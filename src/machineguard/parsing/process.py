"""Worker 专用子进程入口：标准输入接收原文，结果只经管道返回，不保存本地原文替代品。"""

import base64
import json
import sys

from machineguard.server.auth_service import ServiceError
from .config import ParsingConfig
from .pipeline import parse_document


def main():
    try:
        raw = sys.stdin.buffer.read(15 * 1024 * 1024 + 1)
        if len(raw) > 15 * 1024 * 1024:
            raise ServiceError(422, "PARSE_INPUT_LIMIT", "解析输入超出限额。")
        request = json.loads(raw)
        config = ParsingConfig.model_validate(request["config"])
        result = parse_document(base64.b64decode(request["data"], validate=True), request["file_format"], request["mode"], request["title"], config)
        print(json.dumps({"data": result}, ensure_ascii=False))
        return 0
    except ServiceError as error:
        output = {"code": error.code, "message": error.message}
    except Exception:
        output = {"code": "PARSE_PROCESS_FAILED", "message": "独立解析进程失败，未输出原文、凭据或内部异常。"}
    print(json.dumps({"error": output}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
