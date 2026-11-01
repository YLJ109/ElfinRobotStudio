"""Simulation display tool and TCP share one base-frame definition (mm/deg)."""
import math
from .kinematics import RobotModel, TCP_OFFSET_Z_MM, rpy_to_mat
from . import tool_library

DEFAULT_TOOL = {"kind": "vacuum-reference", "rotation": [0, 0, 90],
                "dimensions": {"length": 300, "width": 50, "thickness": 10,
                    "edge": 10, "cupHeight": 50, "cupDiameter": 15, "mountHeight": 0}}

PEN_LENGTH_MM = 160.0
PEN_BODY_LENGTH_MM = 145.0
PEN_RADIUS_MM = 6.0


def normalize(value):
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError("不支持的仿真工具")
    if value.get('kind') == 'pen':
        if set(value)-{'kind','rotation'} or value.get('rotation',[0,0,0]) != [0,0,0]:
            raise ValueError('仿真画笔采用固定安装方向')
        return {'kind':'pen','rotation':[0,0,0]}
    if value.get("kind") == "library":
        item = tool_library.get(value.get("id"))
        if not item:
            raise ValueError("找不到已安装的末端模型")
        rotation = value.get("rotation", item.get("default_rotation_deg", [0,0,0]))
        if not isinstance(rotation,list) or len(rotation)!=3 or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=360 for v in rotation):
            raise ValueError("工具角度需要三个有效角度")
        return {"kind":"library","id":item["id"],"rotation":list(rotation)}
    if value.get("kind") != "vacuum-reference":
        raise ValueError("不支持的仿真工具")
    dimensions = {**DEFAULT_TOOL["dimensions"], **(value.get("dimensions") or {})}
    rotation = value.get("rotation", [0, 0, 90])
    if (not isinstance(rotation, list) or len(rotation) != 3 or
            not all(type(v) in (int, float) and math.isfinite(v) for v in rotation)):
        raise ValueError("工具角度需要三个有限数值")
    if not all(type(dimensions[k]) in (int, float) and math.isfinite(dimensions[k])
               for k in DEFAULT_TOOL["dimensions"]):
        raise ValueError("工具尺寸需要有限数值")
    d = dimensions
    if (not 0 < d["length"] <= 2000 or not 0 < d["width"] <= 1000 or
            not 0 < d["thickness"] <= 500 or not 0 < d["cupHeight"] <= 1000 or
            not 0 < d["cupDiameter"] <= d["width"] or
            not d["cupDiameter"] / 2 <= d["edge"] or
            d["edge"] * 2 + d["cupDiameter"] >= d["length"] or
            not 0 <= d["mountHeight"] <= 1000):
        raise ValueError("工具尺寸无效")
    return {"kind": "vacuum-reference", "rotation": list(rotation), "dimensions": dimensions}


def model_for(variant, tool):
    if tool is None:
        return RobotModel(variant)
    rotation = rpy_to_mat(*map(math.radians, tool["rotation"]))
    if tool['kind'] == 'pen':
        model = RobotModel(variant,tcp_translation_mm=[0,0,PEN_LENGTH_MM-TCP_OFFSET_Z_MM],tcp_rpy_deg=[0,0,0])
        model.pen_tool = True
        model.library_tool = {'rotation':[0,0,0], 'bounds_mm':{'min':[-PEN_RADIUS_MM,-PEN_RADIUS_MM,0], 'max':[PEN_RADIUS_MM,PEN_RADIUS_MM,PEN_LENGTH_MM]}}
        return model
    if tool["kind"] == "library":
        item = tool_library.get(tool["id"])
        translation = [sum(rotation[i][j]*item["tcp_mm"][j] for j in range(3)) for i in range(3)]
        translation[2] -= TCP_OFFSET_Z_MM
        model = RobotModel(variant,tcp_translation_mm=translation,tcp_rpy_deg=tool["rotation"])
        model.library_tool = {"rotation":tool["rotation"],"bounds_mm":tool_library.collision_bounds(item)}
        return model
    d = tool["dimensions"]
    # The selected TCP is the centre of the mounting plate (the user's arrow),
    # rather than a flange origin or the ends of the two suction cups.
    local_z = d["mountHeight"] + d["thickness"] / 2
    translation = [rotation[i][2] * local_z for i in range(3)]
    translation[2] -= TCP_OFFSET_Z_MM
    model = RobotModel(variant, tcp_translation_mm=translation, tcp_rpy_deg=tool["rotation"])
    model.reference_tool = tool
    return model
