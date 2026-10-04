"""定位当前项目，避免误用旧项目的工作目录。"""

from pathlib import Path


def get_project_root() -> Path:
    """根据当前源码位置定位项目根目录，并检查项目标记。"""
    project_root = Path(__file__).resolve().parents[2]
    if not (project_root / "pyproject.toml").is_file():
        raise RuntimeError("当前源码目录没有对应的 pyproject.toml。")
    return project_root
