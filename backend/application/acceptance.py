"""Read immutable build evidence, never infer device qualification from test counts."""
import json


def read_acceptance(path,identity):
    value=None;why='当前构建没有附带验收清单'
    try:
        if path.stat().st_size>128*1024:raise ValueError('验收清单超限')
        candidate=json.loads(path.read_text(encoding='utf-8'))
        if (not isinstance(candidate,dict) or candidate.get('version')!=1
            or not isinstance(candidate.get('features'),list) or len(candidate['features'])>80):raise ValueError('验收清单格式无效')
        for item in candidate['features']:
            if not isinstance(item,dict) or not all(type(item.get(key)) is bool for key in ('source','offline','ui','field')):raise ValueError('验收状态必须明确')
            if any(not isinstance(item.get(key),str) or len(item[key])>160 for key in ('id','title')):raise ValueError('验收名称无效')
            for key in ('unverified','evidence'):
                if not isinstance(item.get(key),list) or len(item[key])>20 or any(not isinstance(v,str) or len(v)>600 for v in item[key]):raise ValueError('验收证据无效')
        value=candidate;why=''
    except FileNotFoundError:pass
    except (OSError,ValueError,TypeError) as exc:why='验收清单不可用：'+str(exc)
    matched=bool(value and identity.get('verified') and all(isinstance(identity.get(key),str) and value.get(key)==identity[key] for key in ('backend_sha256','frontend_sha256')))
    return {'ok':True,'build':dict(identity),'evidence':value,'matches_current':matched,'why':why}
