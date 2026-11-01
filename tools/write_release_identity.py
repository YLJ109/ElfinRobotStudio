"""Bind a local desktop build to current backend, contracts and production UI."""
import argparse
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from backend.build_identity import read_identity

parser=argparse.ArgumentParser()
parser.add_argument('--verify',action='store_true')
args=parser.parse_args()
identity=read_identity()
if not identity['verified']:
    raise SystemExit('Missing production UI identity; build the frontend first')
target=ROOT/'output/runtime/runtime-identity.json'
if args.verify:
    if json.loads(target.read_text(encoding='utf-8'))!=identity:
        raise SystemExit('Source changed during desktop build; rebuild before delivery')
    print('DESKTOP_SOURCE_IDENTITY_OK')
else:
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(identity,indent=2),encoding='utf-8')
    print('DESKTOP_SOURCE_IDENTITY_WRITTEN')
