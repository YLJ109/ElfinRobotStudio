"""Read-only onsite records that gate real MoveLTo; no browser unlock or defaults.

与 real_follow_qualification 同一套思路：代码可以预先就位，但没有一条真实
控制器上跑出来的验证记录，就不允许向硬件下发笛卡尔直线。记录只能由现场
标定流程产生 —— 浏览器、配置文件和环境变量都无法把它"打开"。

记录里的哈希只用来检查一致性，不证明真实性与安全性。
"""
import json
from pathlib import Path

from .planning import digest

# 现场必须量出来的证据项。缺任何一项都不算通过：
#   straightness_mm  实测 TCP 偏离起点-终点直线的最大值
#   settle_s         指令回执后到控制器报 inpos 的时间
#   stop_s           运动中发 GrpStop 到真正停下的时间
REQUIRED_EVIDENCE = ('straightness_mm', 'settle_s', 'stop_s')

# 上限不是"推荐值"，是准入线：超过就不允许用 MoveLTo 走真实轨迹。
MAX_STRAIGHTNESS_MM = 1.0
MAX_SETTLE_S = 2.0
MAX_STOP_S = 2.0


def load_record(path):
    try:
        path = Path(path)
        if path.stat().st_size > 32768:
            return None
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if type(value) is dict else None
    except (OSError, ValueError, TypeError):
        return None


def resolve_record(record, *, version, variant, tool_context, lower, upper, tcp_name, ucs_name):
    def denied(code, detail):
        return dict(ok=False, fingerprint=None,
                    blockers=[dict(layer='controller', code=code,
                                   path='real_movel_qualification', detail=detail)])
    if type(record) is not dict:
        return denied('missing_commissioning',
                      '尚无本控制器的真机直线运动验证记录；先完成一次现场 MoveL 标定')
    if ((record.get('status') != 'verified' or record.get('scope') != 'real'
            or record.get('interface') != 'v6-ascii-movel')
            or any(not isinstance(record.get(k), str) or not record[k].strip()
                   for k in ('record_id', 'verified_by', 'verified_at'))):
        return denied('invalid_commissioning', '真机直线运动验证记录不完整或接口不匹配')
    if (not version or record.get('controller_version') != version
            or record.get('robot_variant') != variant
            or record.get('tool_context') != tool_context
            or record.get('lower') != list(lower) or record.get('upper') != list(upper)):
        return denied('commissioning_context_changed',
                      '控制器版本、工具或关节限位与验证记录不一致')
    if record.get('tcp_name') != tcp_name or record.get('ucs_name') != ucs_name:
        return denied('commissioning_frame_changed',
                      'TCP 或用户坐标系与验证记录不一致；换工具后必须重新标定')
    try:
        evidence = record['evidence']
        straightness = float(evidence['straightness_mm'])
        settle = float(evidence['settle_s'])
        stop = float(evidence['stop_s'])
    except (KeyError, TypeError, ValueError):
        return denied('invalid_evidence', '现场证据缺项或不是数值')
    if not all(v == v and abs(v) != float('inf') for v in (straightness, settle, stop)):
        return denied('invalid_evidence', '现场证据含非有限数值')
    if straightness > MAX_STRAIGHTNESS_MM:
        return denied('straightness_out_of_tolerance',
                      f'实测直线度 {straightness:.2f}mm 超过 {MAX_STRAIGHTNESS_MM:.1f}mm 准入线')
    if settle > MAX_SETTLE_S or stop > MAX_STOP_S:
        return denied('timing_out_of_budget',
                      '到位或停止时间超过准入线；该控制器不能用于直线轨迹')
    try:
        fingerprint = digest(record)
    except (TypeError, ValueError):
        return denied('invalid_record', '验证记录无法计算指纹')
    return dict(ok=True, fingerprint=fingerprint, blockers=[])
