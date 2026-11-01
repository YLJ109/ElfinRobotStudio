"""Build an isolated source snapshot without changing PowerShell policy.

The frontend must have a verified production build. Every package retains its
exact source snapshot and hashes even while later implementation continues.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]


def run(command, *, cwd):
    print('BUILD_STEP '+str(command[0])+' '+str(command[-1]),flush=True)
    subprocess.run([str(value) for value in command],cwd=cwd,check=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installer',action='store_true')
    args=parser.parse_args()
    # The production builder rejects source changes during compilation and
    # validates both browser entrypoints before emitting its identity.
    run(['node',ROOT/'web/scripts/build-production.mjs'],cwd=ROOT/'web')
    runtime=ROOT/'output/runtime'
    runtime.mkdir(parents=True,exist_ok=True)
    sys.path.insert(0,str(ROOT))
    from backend.build_identity import source_digest
    before=source_digest(ROOT)
    stage=Path(tempfile.mkdtemp(prefix='desktop-source-',dir=runtime))
    for relative in ('backend','contracts','desktop','web/dist'):
        shutil.copytree(ROOT/relative,stage/relative,
                        ignore=shutil.ignore_patterns('data','__pycache__','*.pyc'))
    for filename in ('package.json','package-lock.json'):
        shutil.copyfile(ROOT/filename,stage/filename)
    if source_digest(ROOT)!=before or source_digest(stage)!=before:
        raise RuntimeError('Source changed while taking build snapshot; retry after integration')
    identity={'version':1,'backend_sha256':source_digest(stage),
        'frontend_sha256':json.loads((stage/'web/dist/build-identity.json').read_text(encoding='utf-8'))['source_sha256'],
        'verified':True}
    evidence=ROOT/'output/renewal/acceptance-current.json'
    if evidence.is_file():
        from backend.application.acceptance import read_acceptance
        reviewed=read_acceptance(evidence,identity)
        if reviewed['matches_current']:
            shutil.copyfile(evidence,stage/'web/dist/acceptance.json')
            shutil.copyfile(evidence,ROOT/'web/dist/acceptance.json')
        else:
            print('ACCEPTANCE_EVIDENCE_NOT_CURRENT: retained outside package',flush=True)
    (stage/'output/runtime').mkdir(parents=True)
    (stage/'output/runtime/runtime-identity.json').write_text(json.dumps(identity,indent=2),encoding='utf-8')
    original_lock=json.loads((stage/'package-lock.json').read_text(encoding='utf-8'))
    manifest={'snapshot':str(stage),'identity':identity,'files':{
        str(file.relative_to(stage)).replace('\\','/'):hashlib.sha256(file.read_bytes()).hexdigest()
        for file in sorted(stage.rglob('*')) if file.is_file()}}
    (ROOT/'output/renewal').mkdir(parents=True,exist_ok=True)
    (ROOT/'output/renewal/desktop-build-source.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    vision=ROOT/'backend/.venv-vision/Scripts/python.exe'
    if not vision.is_file():raise RuntimeError('Install the isolated vision build environment first')
    for interpreter,spec,dist,work in (
        (vision,'vision_worker.spec','desktop-vision','desktop-vision-build'),
        (Path(sys.executable),'backend.spec','desktop-engine','desktop-build')):
        run([interpreter,'-m','PyInstaller','--noconfirm','--distpath',ROOT/'output'/dist,
             '--workpath',ROOT/'output'/work,stage/'desktop'/spec],cwd=stage)
    package=json.loads((stage/'package.json').read_text(encoding='utf-8'))
    package['build']['directories']['output']=str(ROOT/'output/desktop-release')
    package['build']['electronDist']=str(ROOT/'node_modules/electron/dist')
    package['build']['npmRebuild']=False
    for resource in package['build']['extraResources']:
        # Generated engines live in this workspace; branding is frozen in stage.
        base=ROOT if resource['from'].startswith('output/') else stage
        source=base/resource['from']
        if not source.exists() and resource['from'].startswith('web/public/'):
            source=stage/'web/dist'/Path(resource['from']).relative_to('web/public')
        if not source.exists():raise RuntimeError('Missing packaged resource: '+str(source))
        resource['from']=str(source)
    (stage/'package.json').write_text(json.dumps(package,ensure_ascii=False,indent=2),encoding='utf-8')
    run(['node',ROOT/'node_modules/electron-builder/cli.js','--projectDir',stage,
         '--win', 'nsis' if args.installer else 'dir'],cwd=stage)
    for relative,digest in manifest['files'].items():
        if relative=='package.json':
            if json.loads((stage/relative).read_text(encoding='utf-8'))!=package:
                raise RuntimeError('Packager changed configured app metadata')
            continue
        if relative=='package-lock.json':
            # electron-builder may normalize Windows line endings. Require
            # identical dependency content, retaining the original byte hash.
            if json.loads((stage/relative).read_text(encoding='utf-8'))!=original_lock:
                raise RuntimeError('Packager changed dependency lock content')
            continue
        if hashlib.sha256((stage/relative).read_bytes()).hexdigest()!=digest:
            raise RuntimeError('Staged source changed during packaging: '+relative)
    print('DESKTOP_SNAPSHOT_BUILD_OK '+str(stage),flush=True)


if __name__=='__main__':main()
