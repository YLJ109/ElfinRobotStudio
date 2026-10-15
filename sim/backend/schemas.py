"""Input shape validation; these models do not impose operator travel limits."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, field_validator

Joints = Annotated[list[FiniteFloat], Field(min_length=6, max_length=6)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Move(Input):
    joints: Joints


class IK(Input):
    target: Joints
    seed: Joints | None = None
    allow_partial: bool = False


class Preview(Input):
    joints: Joints | None = None


class Jog(Input):
    axis: Annotated[int, Field(ge=0, le=5, strict=True)]
    direction: Literal[0, 1]
    on: bool


class EndDO(Input):
    bit: Literal[0, 1, 2]
    val: Literal[0, 1]


class Speed(Input):
    pct: Annotated[FiniteFloat, Field(ge=1, le=100)]


Vector3 = Annotated[list[FiniteFloat], Field(min_length=3, max_length=3)]


class FrameCalibration(Input):
    name: Annotated[str, Field(min_length=1, max_length=64)] = "工位"
    save: bool = False
    points: Annotated[list[Vector3], Field(min_length=3, max_length=3)]


class TCPCalibration(Input):
    name: Annotated[str, Field(min_length=1, max_length=64)] = "工具"
    save: bool = False
    poses: Annotated[list[Joints], Field(min_length=4, max_length=100)]


class IOCalibration(Input):
    name: Annotated[str, Field(min_length=1, max_length=64)]
    group: Literal["end", "box"]
    direction: Literal["input", "output"]
    bit: Annotated[int, Field(ge=0, le=7, strict=True)]
    confirmed: bool


class SimInput(Input):
    group: Literal["end", "box"]
    bit: Annotated[int, Field(ge=0, le=7, strict=True)]
    val: Literal[0, 1]


class FrameTarget(Input):
    frame: str
    tool: str | None = None
    target: Joints


class ToolSelect(Input):
    tool_id: Annotated[str, Field(min_length=1, max_length=80)]
    source: Literal["manual", "detected", "controller_config"] = "manual"
    hardware_id: str | None = Field(default=None, max_length=120)
    reason: str = Field(default="", max_length=240)


class ToolClear(Input):
    reason: str = Field(default="用户确认无工具", max_length=240)


class ToolAction(Input):
    action: Literal["open", "close", "vacuum_on", "vacuum_off", "attach", "detach"]
    duration_ms: Annotated[int, Field(ge=0, le=60000, strict=True)] = 0


Pair = Annotated[list[FiniteFloat], Field(min_length=2, max_length=2)]


class Fence(Input):
    x: Pair
    y: Pair
    z: Pair
    enabled: bool = True

    @field_validator("x", "y", "z")
    @classmethod
    def ordered(cls, value):
        if value[0] >= value[1]:
            raise ValueError("围栏下界必须小于上界")
        return value


def validate_settings(patch):
    boolean = {"self_collision", "ground_enabled", "tts", "auto_connect", "real_interpolation"}
    numeric = {"sim_speed_pct", "real_speed_pct", "ghost_opacity", "tts_rate", "log_keep",
               "safety_yellow_pct", "safety_red_pct", "safety_alarm_pct", "ghost_align_tol_deg",
               "ghost_align_hold_s", "free_hold_max_s", "free_force_max_n", "play_len_s", "trail_len"}
    import math
    for key, value in patch.items():
        if key in boolean and type(value) is not bool:
            raise ValueError(key + "需要布尔值")
        if key in numeric:
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(key + "需要有限数值")
            if value < 0 or (key in {"sim_speed_pct", "real_speed_pct"} and not 1 <= value <= 100):
                raise ValueError(key + "数值范围无效")
        if key == "ghost_opacity" and not 0 <= value <= 1:
            raise ValueError("透明度需要0~1")
        if key == "fence":
            Fence.model_validate(value)
        if key == "custom_limits" and value is not None:
            if not isinstance(value, list) or len(value) != 6:
                raise ValueError("需要六组关节限位")
            for pair in value:
                if not isinstance(pair, list) or len(pair) != 2 or not all(type(v) in (int,float) and math.isfinite(v) for v in pair) or pair[0] >= pair[1]:
                    raise ValueError("关节限位必须是有限且递增的数值对")
    enums = {"vel_source": {"urdf", "manual"}, "limit_source": {"controller", "urdf", "manual"},
             "ghost_mode": {"ghost", "live"}, "real_move_cmd": {"relj", "movejto"}}
    for key, choices in enums.items():
        if key in patch and (not isinstance(patch[key], str) or patch[key] not in choices):
            raise ValueError(key + "选项无效")
