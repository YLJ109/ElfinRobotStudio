"""Read-only, server-side commissioning records; no browser unlock or defaults.

Records must be produced by a trusted onsite commissioning workflow. Hashes and
labels check consistency, NOT authenticity or hardware safety certification.
"""
import json
from pathlib import Path

from .planning import digest
from .real_servo import ServoProfile


def load_record(path):
    try:
        path=Path(path)
        if path.stat().st_size>32768:return None
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if type(value) is dict else None
    except (OSError,ValueError,TypeError):return None


def resolve_record(record, *, version,variant,tool_context,lower,upper):
    def denied(code,detail):
        return dict(ok=False,profile=None,fingerprint=None,
                    blockers=[dict(layer='controller',code=code,path='real_follow_qualification',detail=detail)])
    if type(record) is not dict:
        return denied('missing_commissioning','尚无本控制器的在线跟随验证记录')
    if (record.get('status')!='verified' or record.get('scope')!='real'
            or record.get('interface')!='v6-ascii-cycle' or record.get('port')!=10003
            or any(not isinstance(record.get(k),str) or not record[k].strip()
                   for k in ('record_id','verified_by','verified_at'))):
        return denied('invalid_commissioning','在线跟随验证记录不完整或接口不匹配')
    if (not version or record.get('controller_version')!=version
            or record.get('robot_variant')!=variant or record.get('tool_context')!=tool_context
            or record.get('lower')!=list(lower) or record.get('upper')!=list(upper)):
        return denied('commissioning_context_changed','控制器版本、工具或关节限位与验证记录不一致')
    try:
        evidence=record['evidence']
        p=dict(record['profile'])
        if set(p)!={'period_s','lookahead_s','reply_timeout_s','feedback_max_age_s',
                    'target_max_age_s','stop_timeout_s','tracking_error_deg',
                    'start_velocity_deg_s','velocity','acceleration'}:
            raise ValueError('profile fields')
        if p['feedback_max_age_s']>.15 or p['stop_timeout_s']>2.:
            raise ValueError('feedback/stop budgets')
        profile=ServoProfile(**p,lower=tuple(lower),upper=tuple(upper),evidence=tuple(
            evidence[k] for k in ('firmware','tool_load','stop','path_timing')))
        fingerprint=digest(record)
    except (KeyError,ValueError,TypeError,OverflowError):
        return denied('invalid_servo_profile','在线控制时序、速度或停止验证参数无效')
    return dict(ok=True,profile=profile,fingerprint=fingerprint,blockers=[])
