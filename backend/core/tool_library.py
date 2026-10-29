"""Trusted local visualization catalogue. It never configures real hardware."""
import json
import math
from functools import lru_cache
from ..paths import TOOL_CATALOG as CATALOG


@lru_cache(maxsize=1)
def entries():
    if not CATALOG.is_file():
        return ()
    payload = json.loads(CATALOG.read_text(encoding="utf-8"))
    result = []
    for item in payload.get("tools", []):
        asset = item.get("asset", "")
        bounds, tcp = item.get("bounds_mm", {}), item.get("tcp_mm")
        if (not isinstance(item.get("id"), str) or not asset.startswith("/tools/")
                or ".." in asset or not asset.endswith(".glb")):
            raise ValueError("工具库含无效资产标识")
        vectors = [tcp, bounds.get("min"), bounds.get("max")]
        if any(not isinstance(v,list) or len(v)!=3 or not all(type(n) in (int,float) and math.isfinite(n) and abs(n)<=5000 for n in v) for v in vectors):
            raise ValueError("工具库坐标无效")
        if any(a>=b for a,b in zip(bounds["min"],bounds["max"])):
            raise ValueError("工具库包络无效")
        collision_bounds(item)  # Reject malformed swept bounds before caching.
        result.append(item)
    return tuple(result)


def get(tool_id):
    return next((item for item in entries() if item["id"] == tool_id), None)


def collision_bounds(item):
    """Union of bind-pose mesh and certified full visual-preset sweep (mm).

    Independent of action_state: closing may extend beyond open geometry.
    Do not substitute instantaneous animated mesh bounds or alter reference TCP.
    This is conservative geometry, not real-hardware collision qualification.
    """
    original = item["bounds_mm"]
    animation = item.get("animation")
    if animation is None:
        return {key:list(original[key]) for key in ("min","max")}
    bounds = animation.get("collision_bounds_mm", {}) if isinstance(animation,dict) else {}
    if (not isinstance(animation,dict) or animation.get("version") != 1
            or animation.get("simulation_only") is not True
            or animation.get("tcp_policy") != "fixed-reference"
            or any(not isinstance(bounds.get(key),list) or len(bounds[key]) != 3
                or not all(type(v) in (int,float) and math.isfinite(v) and abs(v)<=5000 for v in bounds[key])
                for key in ("min","max"))
            or any(a>=b for a,b in zip(bounds["min"],bounds["max"]))):
        raise ValueError("工具动画完整开合包络无效")
    return {"min":[min(a,b) for a,b in zip(original["min"],bounds["min"])],
            "max":[max(a,b) for a,b in zip(original["max"],bounds["max"])]}


def profile(item):
    actions = {name:"unmapped" for name in (
        ("open","close") if item.get("type")=="parallel_gripper" else ("vacuum_on","vacuum_off"))}
    return {"id":item["id"],"name":item["name"],"type":item["type"],"revision":"asset-reference-v1",
        "tcp":{"name":"模型参考TCP（未实测）","translation_mm":item["tcp_mm"],"rpy_deg":[0,0,0],"status":"unvalidated"},
        "payload":{"mass_kg":None,"com_mm":None,"status":"unknown"},"actions":actions,
        "identity":{"source":"simulation_asset"},"visual":{"kind":"library","id":item["id"]},
        "source":item.get("source"),"license":item.get("license"),"verification":"visual_reference_not_hardware_calibration",
        "simulation_animation":{"available":bool(item.get("animation")),"io":False,
            "tcp_policy":"fixed-reference","collision_bounds_mm":collision_bounds(item),
            "limitations":item.get("animation",{}).get("limitations","Static visual only")}}
