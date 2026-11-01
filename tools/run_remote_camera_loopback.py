"""Dedicated synthetic WebRTC runner. Default offline policy remains unchanged."""
import hashlib,json,os,shutil,subprocess,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
copy=Path(tempfile.mkdtemp(prefix='elfin-rtc-loopback-')).resolve()
for name in ('backend','contracts','tools'):
    shutil.copytree(root/name,copy/name,ignore=shutil.ignore_patterns('data','__pycache__','*.log'))
(copy/'sitecustomize.py').write_text('''import socket,os,builtins,sys
from pathlib import Path
root=Path(os.environ['HUYAN_OFFLINE_ROOT']).resolve()
assert Path(os.environ['ELFIN_DATA_DIR']).resolve().is_relative_to(root)
def allowed(address):
    if not isinstance(address,tuple) or address[0]!='127.0.0.1':raise RuntimeError('Non-loopback network forbidden')
for name in ('bind','connect','connect_ex','sendto'):
    original=getattr(socket.socket,name)
    def wrapper(self,*args,_name=name,_original=original,**kwargs):
        allowed(args[-1] if _name=='sendto' else args[0])
        return _original(self,*args,**kwargs)
    setattr(socket.socket,name,wrapper)
original_import=builtins.__import__
def guarded_import(name,*args,**kwargs):
    result=original_import(name,*args,**kwargs)
    cv=sys.modules.get('cv2')
    if cv is not None:
        def denied(*a,**k):raise RuntimeError('Camera forbidden')
        cv.VideoCapture=denied
    return result
builtins.__import__=guarded_import
os.environ['HUYAN_OFFLINE_RUNTIME_PID']=str(os.getpid())
''',encoding='utf-8')
env=dict(os.environ,HUYAN_NO_REAL='1',HUYAN_OFFLINE_ROOT=str(copy),ELFIN_DATA_DIR=str(copy/'isolated-data'),PYTHONPATH=str(copy),PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
p=subprocess.run([sys.executable,str(copy/'tools/check_remote_camera_loopback.py')],cwd=copy,env=env,capture_output=True,text=True,encoding='utf-8',timeout=90)
evidence=root/'output/checks'/copy.name;evidence.mkdir(parents=True)
(evidence/'run.log').write_text(p.stdout+p.stderr,encoding='utf-8')
(evidence/'report.json').write_text(json.dumps(dict(exit_code=p.returncode,copy=str(copy),source_hashes={str(f.relative_to(copy)):hashlib.sha256(f.read_bytes()).hexdigest() for folder in ('backend','contracts') for f in (copy/folder).rglob('*') if f.is_file()}),indent=2),encoding='utf-8')
print(p.stdout+p.stderr);print('Evidence:',evidence);sys.exit(p.returncode)
