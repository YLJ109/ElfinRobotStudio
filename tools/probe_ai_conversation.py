"""仅用合成短输入检查对话模型，不加载 World、不操作本地服务或机器人。"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main():
    sys.stdout.reconfigure(encoding='utf-8')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-model-request', action='store_true')
    args = parser.parse_args()
    if not args.allow_model_request:
        raise SystemExit('需要 --allow-model-request；仅向项目已配置的官方免费模型发送合成问题。')
    from backend.core import ai_plan, config
    # 只取已有 AI 配置，不加载或保存世界、点位及现场文件。
    path = Path(__file__).resolve().parents[1] / 'backend/data/settings.json'
    saved = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
    model_name = saved.get('ai_model', config.DEFAULTS['ai_model'])
    base = saved.get('ai_base_url', config.DEFAULTS['ai_base_url']).rstrip('/')
    if base != 'https://open.bigmodel.cn/api/paas/v4' or model_name not in ('glm-4-flash', 'glm-4.7-flash'):
        raise SystemExit('配置不是本探测白名单中的官方端点和免费模型，未发送请求。')
    key = config.zhipu_key()
    if not key:
        # 只读当前用户变量，不修改注册表，也不输出凭据。
        import os
        if os.name == 'nt':
            import winreg
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as handle:
                    key = str(winreg.QueryValueEx(handle, 'ZHIPU_API_KEY')[0]).strip()
            except OSError:
                pass
    if not key:
        print(json.dumps({'ok': False, 'reason': 'missing_credentials'}))
        return 1
    settings = {'mode': 'sim', 'ai_model': model_name, 'ai_base_url': base,
                'sim_speed_pct': 10, 'real_speed_pct': 5}
    model = SimpleNamespace(lower=[-360] * 6, upper=[360] * 6)
    points = [{'id': 'synthetic-1', 'name': '合成点1', 'code': 'P1', 'joints': [0] * 6},
              {'id': 'synthetic-3', 'name': '合成点3', 'code': 'P3', 'joints': [1] * 6}]
    cases = [
        ('greeting_local', '你好', 'chat', False),
        ('ambiguous_process_local', '吸取物体，放在P3，回起点', 'clarify', False),
        ('explicit_plan_local', '速度10%，移动到P1，然后移动到P3', 'plan', False),
        ('conversation_real_model', '请用一句话解释机器人程序中的WAIT，不要生成动作。', 'chat', True),
    ]
    passed = True
    for name, text, expected, external in cases:
        logs = []
        started = time.monotonic()
        try:
            result = await asyncio.wait_for(ai_plan.make_plan(text, [], points, model, settings, key,
                log=lambda kind, message: logs.append((kind, message))), timeout=35)
            called_model = any('模型原始输出' in message for _, message in logs)
            ok = (result.get('ok') is True and result.get('kind') == expected
                  and bool(result.get('plan')) == (expected == 'plan')
                  and called_model == external)
            if external:
                ok = ok and ('秒' in str(result.get('summary')) or '时间' in str(result.get('summary')))
            print(json.dumps({'case': name, 'ok': ok, 'model': model_name,
                'actual_model_response': called_model, 'kind': result.get('kind'),
                'plan_steps': len(result.get('plan') or []),
                'elapsed_ms': round((time.monotonic() - started) * 1000),
                'summary': str(result.get('summary') or result.get('error') or '')[:160]}, ensure_ascii=False))
        except Exception as exc:
            ok = False
            print(json.dumps({'case': name, 'ok': False, 'error_type': type(exc).__name__}))
        passed = passed and ok
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
