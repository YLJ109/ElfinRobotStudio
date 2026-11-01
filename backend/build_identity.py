"""Read-only code/contract identity, captured once when a service is imported."""
import hashlib
import json
from pathlib import Path
import sys


def source_digest(root):
    paths = [*root.joinpath('backend').rglob('*.py'), *root.joinpath('contracts').rglob('*.json')]
    rows = sorted((str(path.relative_to(root)).replace('\\','/'),
                   hashlib.sha256(path.read_bytes()).hexdigest()) for path in paths)
    return hashlib.sha256(''.join(name+'\0'+digest+'\n' for name,digest in rows).encode()).hexdigest()


def read_identity():
    root=Path(__file__).resolve().parents[1]
    if getattr(sys,'frozen',False):
        try:return json.loads((Path(sys._MEIPASS)/'runtime-identity.json').read_text(encoding='utf-8'))
        except (OSError,ValueError):return {'version':1,'backend_sha256':None,'frontend_sha256':None,'verified':False}
    try:
        frontend=json.loads((root/'web/dist/build-identity.json').read_text(encoding='utf-8'))['source_sha256']
    except (OSError,ValueError,KeyError):frontend=None
    return {'version':1,'backend_sha256':source_digest(root),'frontend_sha256':frontend,'verified':frontend is not None}


IDENTITY=read_identity()
