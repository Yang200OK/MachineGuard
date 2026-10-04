"""独立业务配置，不改变已验收的训练配置与身份设置。"""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from machineguard.paths import get_project_root


class BusinessConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    bundle_directory: str = Field(min_length=1)
    demo_engine_ids: list[int] = Field(min_length=5, max_length=10)
    risk_version: str = Field(pattern=r"^[a-z0-9_]{1,64}$")
    critical_max: float = Field(ge=0, allow_inf_nan=False)
    warning_max: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def check(self):
        if len(set(self.demo_engine_ids)) != len(self.demo_engine_ids) or any(not 1 <= x <= 100 for x in self.demo_engine_ids):
            raise ValueError("示例设备编号必须唯一且在 1 至 100 之间。")
        if self.warning_max <= self.critical_max:
            raise ValueError("关注阈值必须大于高风险阈值。")
        return self

    @property
    def bundle_path(self) -> Path:
        return (get_project_root() / self.bundle_directory).resolve()

    @property
    def risk_rule(self) -> dict:
        return {"version": self.risk_version, "critical_max": self.critical_max,
                "warning_max": self.warning_max, "unit": "运行周期",
                "limitation": "演示业务阈值，不是故障概率或已确认故障。"}


def load_business_configuration() -> BusinessConfiguration:
    return BusinessConfiguration.model_validate(json.loads(
        (get_project_root() / "configs/business.json").read_text(encoding="utf-8")))
