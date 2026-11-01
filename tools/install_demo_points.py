"""Append the ten checked demonstration poses, preserving existing point identities."""
import json
from pathlib import Path
from install_demo_programs import request

ROOT=Path(__file__).resolve().parents[1]
FOLDER=ROOT/'output/demo-library-20261031/131645'

def main():
    library=json.loads((FOLDER/'library.json').read_text(encoding='utf-8'))
    points=request('points')['items']
    state=request('state')
    assert state['mode']=='sim', 'Only simulation storage is permitted'
    rows=[dict(name=f'演示{i:02d} · '+p['note'].split('：')[-1],joints=p['joints'])
          for i,p in enumerate(library['points'],1)]
    existing={p['name']:p for p in points}
    matching=[existing.get(row['name']) for row in rows]
    if any(matching):
        assert all(p and p['joints']==r['joints'] and p['source']=='sim' for p,r in zip(matching,rows)), 'Existing demonstration names conflict; no data changed'
        print('Already installed:', ', '.join(p['code'] for p in matching))
        return
    backup=FOLDER/'before-points-install.json'
    assert not backup.exists(), 'Previous installation recovery record exists; inspect before retrying'
    programs=request('programs')
    backup.write_text(json.dumps(dict(points=points,programs=programs),ensure_ascii=False,indent=2),encoding='utf-8')
    response=request('teaching/batch',dict(points=rows))
    assert response.get('ok'), response
    added=response['items']
    after=request('points')['items']
    old_by_id={p['id']:p for p in after}
    assert all(old_by_id.get(p['id'])==p for p in points), 'Original point data changed'
    assert len(after)==len(points)+10
    assert len(added)==10 and all(p['source']=='sim' for p in added)
    assert request('programs')==programs, 'Program library changed'
    # Read the persisted store independently; no service restart or movement.
    disk=json.loads((ROOT/'backend/data/points.json').read_text(encoding='utf-8'))
    assert all(next((p for p in disk if p['id']==a['id']),None)==a for a in added)
    result=dict(added=added,original_points_preserved=True,persisted_store_verified=True,
                programs_unchanged=True,validation='output/checks/huayan-fix-checks-gx6okueh.json')
    (FOLDER/'points-installed.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(added=[dict(code=p['code'],name=p['name']) for p in added],original_points_preserved=True),ensure_ascii=False))

if __name__=='__main__':main()
