"""Install checked examples using the normal local program API, without motion."""
import datetime
import json
from pathlib import Path
import urllib.parse
import urllib.request

ROOT=Path(__file__).resolve().parents[1]

def request(path,payload=None):
    body=None if payload is None else json.dumps(payload,ensure_ascii=False).encode('utf-8')
    req=urllib.request.Request('http://127.0.0.1:8710/api/'+path,data=body,
                               headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as response:
        return json.load(response)

def main():
    report=json.loads((ROOT/'output/checks/huayan-fix-checks-gx6okueh.json').read_text(encoding='utf-8'))
    result=next(row for row in report['tests'] if row['test']=='check_demo_library.py')
    assert result['exit_code']==0
    log=Path(result['log']).read_text(encoding='utf-8')
    library=json.loads(next(line.removeprefix('DEMO_LIBRARY_JSON ') for line in log.splitlines()
                            if line.startswith('DEMO_LIBRARY_JSON ')))
    state=request('state')
    assert state['mode']=='sim', 'Only simulation installation is permitted'
    current=request('programs')['items']
    saved={row['name']:request('programs/'+urllib.parse.quote(row['name']))['src'] for row in current}
    for row in library['programs']:
        assert row['name'] not in saved or saved[row['name']]==row['src'], 'Existing program would be overwritten'
    folder=ROOT/'output/demo-library-20261031'/datetime.datetime.now().strftime('%H%M%S')
    folder.mkdir(parents=True,exist_ok=False)
    before=dict(points=request('points'),programs=saved)
    (folder/'before-install.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
    (folder/'library.json').write_text(json.dumps(library,ensure_ascii=False,indent=2),encoding='utf-8')
    for row in library['programs']:
        if row['name'] not in saved:
            response=request('programs',row)
            assert response.get('ok'), response
        assert request('programs/'+urllib.parse.quote(row['name']))['src']==row['src']
    after=request('points')
    assert after==before['points'], 'Point data changed during program installation'
    (folder/'installed.json').write_text(json.dumps(dict(programs=10,points_replaced=False,
                          report='output/checks/huayan-fix-checks-gx6okueh.json'),indent=2),encoding='utf-8')
    print(json.dumps(dict(folder=str(folder),programs=10,points_replaced=False)))

if __name__=='__main__':main()
