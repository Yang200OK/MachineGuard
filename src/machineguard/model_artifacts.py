"""模型产物的原子写入、哈希与受限清单加载。"""

from hashlib import sha256
import json
from pathlib import Path

from .training import TrainingError


def file_record(path: Path) -> dict:
    """清单记录文件名、大小和 SHA-256。"""
    content = path.read_bytes()
    return {"file": path.name, "size_bytes": len(content), "sha256": sha256(content).hexdigest()}


def write_json(payload: dict | list, path: Path) -> None:
    """清单最后发布；单个 JSON 文件以原子替换更新。"""
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def publish_manifest(payload: dict, directory: Path) -> None:
    """清单自身哈希单独保存，避免递归包含自身。"""
    path = directory / "model_manifest.json"
    write_json(payload, path)
    temporary = directory / "model_manifest.sha256.tmp"
    temporary.write_text(f"{file_record(path)['sha256']}  model_manifest.json\n", encoding="utf-8")
    temporary.replace(directory / "model_manifest.sha256")


def verify_manifest(directory: Path, schema_version: str) -> dict:
    """启动时核对全部清单文件，禁止路径逃逸和静默使用损坏产物。"""
    try:
        content = (directory / "model_manifest.json").read_bytes()
        expected = (directory / "model_manifest.sha256").read_text(encoding="utf-8").split()
        if expected != [sha256(content).hexdigest(), "model_manifest.json"]:
            raise TrainingError("模型清单自身 SHA-256 校验失败。")
        payload = json.loads(content.decode("utf-8"))
        if payload["schema_version"] != schema_version:
            raise TrainingError("模型清单版本不匹配。")
        files = payload["files"]
        if not isinstance(files, list) or not files or len({item["file"] for item in files}) != len(files):
            raise TrainingError("模型文件清单为空或重复。")
        for item in files:
            name = item["file"]
            if not isinstance(name, str) or Path(name).name != name or name in (".", ".."):
                raise TrainingError("模型清单包含非法路径。")
            if (directory / name).resolve().parent != directory.resolve():
                raise TrainingError("模型文件必须位于模型目录内。")
            if file_record(directory / name) != item:
                raise TrainingError(f"模型产物 SHA-256 或大小不匹配：{name}")
        return payload
    except (OSError, UnicodeError, json.JSONDecodeError, KeyError, TypeError) as error:
        raise TrainingError(f"无法核对模型清单：{directory}；{error}") from error
