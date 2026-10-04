"""从私有 OSS 读取固定原文，在可取消、有时限的独立子进程中解析。"""

import base64
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import UUID

from machineguard.parsing.config import ParsingConfig
from machineguard.server.auth_service import ServiceError
from machineguard.server.document_models import DocumentVersion
from machineguard.server.job_service import JobInterrupted
from machineguard.server.oss_storage import OssStorage
from machineguard.server.parse_models import DocumentParse


def document_parse(context, payload):
    context.stage("read_original", 15)
    with context.database.sessions() as session:
        run = session.get(DocumentParse, UUID(payload["parse_id"]))
        version = session.get(DocumentVersion, UUID(payload["version_id"]))
        if run is None or version is None or run.job_id != context.lease.job_id or run.version_id != version.id or \
                version.status != "uploaded" or version.sha256 != payload["source_sha256"] or run.mode != payload["mode"]:
            raise ServiceError(409, "PARSE_SOURCE_CHANGED", "任务固定的原文或解析模式不一致。")
        config = ParsingConfig.model_validate(run.config_snapshot)
        if config.frozen()[1] != payload["config_sha256"] or run.config_sha256 != payload["config_sha256"]:
            raise ServiceError(409, "PARSE_CONFIG_CHANGED", "解析配置快照校验失败。")
        bucket, region, key, size, digest, suffix = version.oss_bucket, version.oss_region, version.object_key, version.size_bytes, version.sha256, version.file_format
        title = run.source_metadata["title"]
    store = OssStorage()
    if store.bucket != bucket or store.settings.MG_OSS_REGION != region:
        raise ServiceError(503, "OSS_CONFIGURATION_CHANGED", "Worker OSS 与原文冻结配置不一致。")
    store.check_bucket(); data = store.read_verified(key, size, digest)
    context.stage("parse_native" if run.mode == "native" else "parse_vision", 40)
    request = json.dumps({"config": config.model_dump(by_alias=True), "data": base64.b64encode(data).decode(),
        "file_format": suffix, "mode": run.mode, "title": title}, ensure_ascii=False).encode()
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    # 参数固定，不接受文档中的命令、文件路径、URL 或脚本。原文仅在受限内存/管道中传递。
    process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "machineguard.parsing.process"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    started = time.monotonic(); sent = False
    try:
        while True:
            if context.interrupted.is_set():
                raise JobInterrupted()
            if time.monotonic() - started > config.timeout_seconds:
                raise ServiceError(503, "PARSE_PROCESS_TIMEOUT", "解析子进程超时，已终止。")
            try:
                output, _ = process.communicate(input=request if not sent else None, timeout=1)
                break
            except subprocess.TimeoutExpired:
                sent = True
                # 同时核对总时限、权限、取消和租约；心跳线程不代替这个阶段检查。
                context.stage("parse_native" if run.mode == "native" else "parse_vision", 50)
        if len(output) > 8 * 1024 * 1024:
            raise ServiceError(503, "PARSE_OUTPUT_LIMIT", "解析进程输出超过限额。")
        result = json.loads(output.decode("utf-8", errors="strict"))
        if process.returncode != 0:
            error = result["error"]
            if not isinstance(error["code"], str) or not error["code"].startswith("PARSE_") or len(error["message"]) > 1000:
                raise ValueError("解析失败契约非法。")
            raise ServiceError(422, error["code"], error["message"])
        parsed = result["data"]
        canonical = {"pages": parsed["pages"], "chunks": parsed["chunks"]}
        expected = sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()
        if expected != parsed["result_sha256"] or not parsed["pages"] or not parsed["chunks"]:
            raise ValueError("解析结果哈希或正文非法。")
        context.stage("save_parse", 90)
        return parsed
    except (ValueError, KeyError, TypeError, UnicodeError):
        raise ServiceError(503, "PARSE_PROCESS_OUTPUT_INVALID", "解析进程输出不符合约定。") from None
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()
