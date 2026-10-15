"""把官方 elfin5 URDF/xacro 转成前端能直接用的单一 JSON，并把 STL 网格拷进 web 资源目录。

为什么不用现成的 URDF 加载器：前端只需要 层级 + 原点 + 轴 + 限位 + 网格 + 颜色，
自己解析一遍可以把 xacro 表达式（${PI/2} 之类）算准，并且顺带产出一份"限位来自哪里"的清单。

跑法：python tools/build_robot_model.py [--variant elfin5]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor_elfin" / "elfin_description"
OUT_JSON_DIR = ROOT / "sim" / "web" / "public" / "models"

XACRO_PROPS = {"PI": 3.141592653589793}
_EXPR = re.compile(r"\$\{([^}]*)\}")


def eval_xacro(text):
    """求值 ${...} 形式的 xacro 表达式；只允许数字和四则运算，不 eval 任意代码。"""
    if text is None:
        return None

    def sub(m):
        expr = m.group(1).strip()
        if not re.fullmatch(r"[0-9a-zA-Z_+\-*/(). ]+", expr):
            raise ValueError(f"表达式含不允许的字符：{expr!r}")
        names = {k: v for k, v in XACRO_PROPS.items() if re.search(rf"\b{k}\b", expr)}
        try:
            val = _safe_eval(expr, names)
        except Exception as e:
            raise ValueError(f"无法求值 ${{{expr}}}: {e}")
        return repr(val)

    out = _EXPR.sub(sub, str(text))
    return out


def _safe_eval(expr, names):
    import ast
    import operator as op
    ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv,
           ast.Pow: op.pow, ast.USub: op.neg, ast.UAdd: op.pos}

    def ev(node):
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return node.value
            raise ValueError(f"非常量：{node.value!r}")
        if isinstance(node, ast.Name):
            if node.id in names:
                return names[node.id]
            raise ValueError(f"未知变量：{node.id}")
        if isinstance(node, ast.BinOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in ops:
            return ops[type(node.op)](ev(node.operand))
        raise ValueError(f"不支持的语法节点：{type(node).__name__}")

    return ev(ast.parse(expr, mode="eval").body)


def nums(text, n):
    v = [float(x) for x in eval_xacro(text).replace(",", " ").split()]
    if len(v) != n:
        raise ValueError(f"需要 {n} 个数，实际 {v}")
    return v


def strip_ns(tag):
    return tag.split("}")[-1]


def parse_materials(path):
    mats = {}
    root = ET.parse(path).getroot()
    for m in root:
        if strip_ns(m.tag) != "material":
            continue
        col = next((c for c in m if strip_ns(c.tag) == "color"), None)
        if col is None:
            continue
        rgba = nums(col.get("rgba"), 4)
        mats[m.get("name")] = {"rgba": rgba}
    return mats


def parse_robot(xacro_path, materials):
    root = ET.parse(xacro_path).getroot()
    links, joints = {}, []
    for el in root:
        tag = strip_ns(el.tag)
        if tag == "link":
            name = el.get("name")
            info = {"name": name, "visual": None, "collision_mesh": None}
            for ch in el:
                ctag = strip_ns(ch.tag)
                if ctag == "visual":
                    org = next((o for o in ch if strip_ns(o.tag) == "origin"), None)
                    geo = next((g for g in ch if strip_ns(g.tag) == "geometry"), None)
                    mesh = next((m for m in geo if strip_ns(m.tag) == "mesh"), None) if geo is not None else None
                    mat = next((m for m in ch if strip_ns(m.tag) == "material"), None)
                    if mesh is None:
                        continue
                    mname = mat.get("name") if mat is not None else None
                    if mname is None and mat is not None:
                        inner = next((c for c in mat if strip_ns(c.tag) == "color"), None)
                        if inner is not None:
                            mname = f"_inline_{name}"
                            materials[mname] = {"rgba": nums(inner.get("rgba"), 4)}
                    info["visual"] = {
                        "mesh": mesh.get("filename").split("/")[-1],
                        "origin_xyz": nums(org.get("xyz", "0 0 0"), 3) if org is not None else [0, 0, 0],
                        "origin_rpy": nums(org.get("rpy", "0 0 0"), 3) if org is not None else [0, 0, 0],
                        "material": mname,
                    }
                elif ctag == "collision":
                    geo = next((g for g in el if strip_ns(g.tag) == "collision"), None)
            links[name] = info
        elif tag == "joint":
            jtype = el.get("type")
            org = next((o for o in el if strip_ns(o.tag) == "origin"), None)
            axis = next((a for a in el if strip_ns(a.tag) == "axis"), None)
            lim = next((l for l in el if strip_ns(l.tag) == "limit"), None)
            parent = next((p for p in el if strip_ns(p.tag) == "parent"), None)
            child = next((c for c in el if strip_ns(c.tag) == "child"), None)
            j = {
                "name": el.get("name"),
                "type": jtype,
                "parent": parent.get("link") if parent is not None else None,
                "child": child.get("link") if child is not None else None,
                "xyz": nums(org.get("xyz", "0 0 0"), 3) if org is not None else [0, 0, 0],
                "rpy": nums(org.get("rpy", "0 0 0"), 3) if org is not None else [0, 0, 0],
                "axis": nums(axis.get("xyz", "0 0 1"), 3) if axis is not None else [0, 0, 1],
            }
            if lim is not None:
                j["limit"] = {
                    "lower": float(lim.get("lower")), "upper": float(lim.get("upper")),
                    "velocity": float(lim.get("velocity", 0)), "effort": float(lim.get("effort", 0)),
                }
            joints.append(j)
    return links, joints


def mesh_bbox(path):
    """解析二进制 STL 求包围盒和质心（连杆坐标系下）。碰撞包络和相机取景都要用。"""
    import struct
    data = path.read_bytes()
    if len(data) < 84:
        return None
    n = struct.unpack_from("<I", data, 80)[0]
    if 84 + n * 50 != len(data):
        return None            # 不是标准二进制 STL，不猜
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    total = [0.0] * 3
    count = 0
    for i in range(n):
        base = 84 + i * 50
        for v in range(3):
            p = struct.unpack_from("<3f", data, base + 12 + v * 12)
            for k in range(3):
                lo[k] = min(lo[k], p[k])
                hi[k] = max(hi[k], p[k])
                total[k] += p[k]
            count += 1
    if not count:
        return None
    return {"min": [round(v, 6) for v in lo], "max": [round(v, 6) for v in hi],
            "center": [round(total[k] / count, 6) for k in range(3)],
            "size": [round(hi[k] - lo[k], 6) for k in range(3)],
            "triangles": n}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", default="elfin5")
    a = ap.parse_args()

    mesh_dir_src_pre = VENDOR / "meshes" / a.variant
    materials = parse_materials(VENDOR / "urdf" / "materials.xacro")
    links, joints = parse_robot(VENDOR / "urdf" / f"{a.variant}.urdf.xacro", materials)

    movable = [j for j in joints if j.get("limit")]
    out = {
        "source": {
            "repo": "https://github.com/huayan-robotics/elfin_robot",
            "file": f"elfin_description/urdf/{a.variant}.urdf.xacro",
            "note": "限位与关节几何全部来自官方 URDF；与手册表格不一致时以本文件为准并在此注明",
        },
        "variant": a.variant,
        "materials": materials,
        "joints": joints,
        "links": links,
        "movable_joints": [j["name"] for j in movable],
    }

    print(f"变体 {a.variant}：{len(joints)} 关节 / {len(links)} 连杆，其中可动 {len(movable)}")
    import math
    for j in movable:
        lo, up = j["limit"]["lower"], j["limit"]["upper"]
        print(f"  {j['name']:16} {math.degrees(lo):7.1f}° ~ {math.degrees(up):7.1f}°   "
              f"vmax={j['limit']['velocity']:.2f} rad/s ({math.degrees(j['limit']['velocity']):.0f}°/s)")

    for lname, l in links.items():
        v = l.get("visual")
        if not v:
            continue
        stl = mesh_dir_src_pre / v["mesh"]
        v["bbox"] = mesh_bbox(stl) if stl.exists() else None
        if v["bbox"]:
            sz = v["bbox"]["size"]
            print(f"  {lname:18} 网格 {v['mesh']:26} 尺寸 {sz[0]*1000:6.1f} x {sz[1]*1000:6.1f} x {sz[2]*1000:6.1f} mm  "
                  f"三角面 {v['bbox']['triangles']}")

    mesh_dir_src = VENDOR / "meshes" / a.variant
    dst = OUT_JSON_DIR / a.variant
    dst.mkdir(parents=True, exist_ok=True)
    copied = 0
    for stl in sorted(mesh_dir_src.glob("*.STL")):
        shutil.copy2(stl, dst / stl.name)
        copied += 1
    (dst / "robot.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"复制网格 {copied} 个 -> {dst}")

    missing = {l["visual"]["mesh"] for l in links.values() if l.get("visual")} - \
              {p.name for p in dst.glob("*.STL")}
    if missing:
        print("!! JSON 里引用但没拷到的网格：", sorted(missing))
    else:
        print("网格引用完整性：OK")


if __name__ == "__main__":
    main()
