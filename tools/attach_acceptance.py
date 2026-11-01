"""Attach reviewed, version-matching evidence to an unpacked local build.

Only the generated acceptance.json changes. Executables, JavaScript and backend
code are verified and retained, so final test evidence need not trigger a second
compilation of already tested code. No installer/signing/publication is performed.
"""
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.application.acceptance import read_acceptance
from backend.build_identity import source_digest


def main():
    manifest_path=ROOT/'output/renewal/desktop-build-source.json'
    original=manifest_path.read_bytes();manifest=json.loads(original);identity=manifest['identity']
    stage=Path(manifest['snapshot']).resolve()
    if not stage.is_relative_to((ROOT/'output/runtime').resolve()):raise ValueError('构建副本不在指定目录')
    if source_digest(ROOT)!=identity['backend_sha256']:raise ValueError('后端源码已变化，请重新构建')
    if json.loads((ROOT/'web/dist/build-identity.json').read_text())['source_sha256']!=identity['frontend_sha256']:raise ValueError('前端构建已变化')
    evidence_path=ROOT/'output/renewal/acceptance-current.json'
    evidence=read_acceptance(evidence_path,identity)
    if not evidence['matches_current']:raise ValueError('验收清单不对应当前构建')
    for relative,digest in manifest['files'].items():
        if relative in ('package.json','package-lock.json','web/dist/acceptance.json'):continue
        file=(stage/relative).resolve()
        if not file.is_relative_to(stage) or hashlib.sha256(file.read_bytes()).hexdigest()!=digest:raise ValueError('副本文件变化：'+relative)
    engine=ROOT/'output/desktop-engine/ElfinBackend/_internal'
    packaged=ROOT/'output/desktop-release/win-unpacked/resources/backend/_internal'
    for base in (engine,packaged):
        if json.loads((base/'runtime-identity.json').read_text())!=identity:raise ValueError('运行产物身份不匹配')
        for file in (stage/'web/dist').rglob('*'):
            if file.is_file() and file.name!='acceptance.json':
                target=base/'web/dist'/file.relative_to(stage/'web/dist')
                if not target.is_file() or hashlib.sha256(target.read_bytes()).digest()!=hashlib.sha256(file.read_bytes()).digest():raise ValueError('产物页面与副本不一致')
    payload=evidence_path.read_bytes();digest=hashlib.sha256(payload).hexdigest()
    for destination in (ROOT/'web/dist/acceptance.json',stage/'web/dist/acceptance.json',engine/'web/dist/acceptance.json',packaged/'web/dist/acceptance.json'):
        if not destination.resolve().is_relative_to(ROOT):raise ValueError('证据路径越界')
        temporary=destination.with_suffix('.json.tmp');temporary.write_bytes(payload);temporary.replace(destination)
    manifest['files']['web/dist/acceptance.json']=digest
    manifest['acceptance_attachment']={'sha256':digest,'previous_manifest_sha256':hashlib.sha256(original).hexdigest(),
        'executables_or_code_changed':False,'input':'output/renewal/acceptance-current.json'}
    manifest_path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print('ACCEPTANCE_ATTACHED_VERIFIED '+digest)


if __name__=='__main__':main()
