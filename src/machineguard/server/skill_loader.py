"""项目运行时 Skill 注册表；版本与哈希来自仓库审核记录。"""

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from machineguard.paths import get_project_root
from .auth_service import ServiceError


SKILL_NAMES = frozenset({"engine-analysis", "sensor-trend-analysis", "maintenance-qa",
                         "report-generation", "evidence-path-analysis"})
VERSION = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"
HASH = r"^[0-9a-f]{64}$"


class SkillRelease(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    version: str = Field(pattern=VERSION)
    sha256: str = Field(pattern=HASH)


class SkillRegistration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    name: str
    active_version: str = Field(pattern=VERSION)
    releases: tuple[SkillRelease, ...]

    @model_validator(mode="after")
    def release_identity(self):
        versions = [item.version for item in self.releases]
        if self.name not in SKILL_NAMES or len(versions) != len(set(versions)) or self.active_version not in versions:
            raise ValueError("Skill 名称、版本唯一性或启用版本不符合注册规则。")
        return self


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)
    schema_version: Literal["machineguard.skills.v1"] = Field(alias="schema")
    skills: tuple[SkillRegistration, ...]

    @model_validator(mode="after")
    def complete_registry(self):
        if len(self.skills) != 5 or {item.name for item in self.skills} != SKILL_NAMES:
            raise ValueError("注册表必须包含五个唯一的项目 Skill。")
        return self


@dataclass(frozen=True)
class SkillMetadata:
    name: str
    description: str
    version: str
    sha256: str
    path: Path


@dataclass(frozen=True)
class LoadedSkill:
    name: str
    description: str
    version: str
    sha256: str
    body: str


def checked_file(root, file):
    # Windows 路径和链接也必须留在仓库 Skill 根目录内。
    if file.is_symlink() or any(parent.is_symlink() for parent in file.parents if parent != root.parent):
        raise ValueError("Skill 资源不允许使用符号链接。")
    resolved = file.resolve(strict=True)
    if not resolved.is_relative_to(root.resolve(strict=True)) or not resolved.is_file():
        raise ValueError("Skill 文件超出注册目录。")
    data = resolved.read_bytes()
    if len(data) > 32768:
        raise ValueError("Skill 文件超过大小限制。")
    content = data.decode("utf-8", errors="strict")
    if "\ufffd" in content or "\u951f\u65a4\u62f7" in content or re.search(r"[\U0001f300-\U0001faff]", content):
        raise ValueError("Skill 文件含乱码或不允许的字符。")
    return content, sha256(data).hexdigest()


def metadata(content):
    # 只支持三个明确字段，正文与目录文本均不能注册工具或改变权限。
    match = re.match(r"\A---\nname: ([a-z-]+)\ndescription: ([^\n]+)\nversion: ([^\n]+)\n---\n", content)
    if match is None or not content[match.end():].strip():
        raise ValueError("Skill 必须使用约定的元数据头和非空正文。")
    return match.groups(), content[match.end():]


class SkillRegistry:
    def __init__(self, project_root=None):
        project_root = get_project_root() if project_root is None else Path(project_root)
        self.root = project_root / "skills"
        manifest = SkillManifest.model_validate_json((project_root / "configs/skills.json").read_bytes())
        self.manifest_sha256 = sha256(manifest.model_dump_json(by_alias=True).encode("utf-8")).hexdigest()
        entries = {}; active = {}
        for registration in manifest.skills:
            for release in registration.releases:
                file = self.root / registration.name / "versions" / release.version / "SKILL.md"
                content, actual_hash = checked_file(self.root, file)
                fields, _ = metadata(content)
                if fields[0] != registration.name or fields[2] != release.version or actual_hash != release.sha256:
                    raise ValueError("Skill 版本正文与审核哈希不一致。")
                entries[(registration.name, release.version)] = SkillMetadata(*fields, actual_hash, file)
            canonical = self.root / registration.name / "SKILL.md"
            _, actual_hash = checked_file(self.root, canonical)
            item = entries[(registration.name, registration.active_version)]
            if actual_hash != item.sha256:
                raise ValueError("Skill 入口与启用版本副本不一致。")
            active[registration.name] = registration.active_version
        self.entries = MappingProxyType(entries)
        self.active = MappingProxyType(active)

    def describe(self, name, version=None):
        if name not in self.active:
            raise ServiceError(404, "SKILL_NOT_FOUND", "未注册此 Skill。")
        version = self.active[name] if version is None else version
        item = self.entries.get((name, version))
        if item is None:
            raise ServiceError(404, "SKILL_VERSION_NOT_FOUND", "未注册此 Skill 版本。")
        return item

    def load(self, name, version=None):
        item = self.describe(name, version)
        try:
            content, actual_hash = checked_file(self.root, item.path)
            if actual_hash != item.sha256:
                raise ValueError("Skill 运行期间发生变更。")
            fields, body = metadata(content)
            if fields != (item.name, item.description, item.version):
                raise ValueError("Skill 元数据发生变更。")
        except (OSError, UnicodeError, ValueError):
            raise ServiceError(503, "SKILL_SOURCE_CHANGED", "Skill 来源已改变或不可读；请审核新版本并重启服务。") from None
        # 选择后正文为不可变字符串，后续文件变化不会替换当前运行的 Skill。
        return LoadedSkill(item.name, item.description, item.version, item.sha256, body)
