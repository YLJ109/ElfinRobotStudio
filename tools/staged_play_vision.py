"""Pure camera evidence binding and bounded tray geometry; no device access."""
import copy
import math


def piece_width(counts):
    return 72 if sum(counts.values()) <= 9 and max(counts.values()) <= 3 else 38


def camera_identity(snapshot):
    """Stable camera ownership, separate from the virtual camera pose hash."""
    source=(snapshot.get('config') or {}).get('input')
    result=snapshot.get('result') or {}
    key=result.get('source_key')
    if source=='virtual':
        session=snapshot.get('virtual_session')
        if not isinstance(session,str) or not session:return None
        if result and (result.get('source')!='virtual' or not isinstance(key,str)
                       or not key.startswith('virtual:'+session+':')):return None
        return {'source':'virtual','session':session}
    if source=='camera':
        active=snapshot.get('camera_identity') or {}
        session=active.get('session');device=active.get('physical_device_id') or active.get('id')
        if not session or not device or active.get('state')!='streaming':return None
        if result and (result.get('source')!='camera' or key!='camera:'+session):return None
        return {'source':'camera','session':session,'id':active.get('id'),
                'physical_device_id':device,'kind':active.get('kind')}
    return None


def bind_observations(snapshot, pieces, now):
    result = snapshot.get('result') or {}
    calibration = snapshot.get('calibration') or {}
    config = snapshot.get('config') or {}
    if not config.get('enabled') or not result:
        raise ValueError('分拣缺少视觉观测：' + (snapshot.get('error') or '请开启视觉并采集A区图像'))
    if result.get('source') not in ('camera', 'virtual'):
        raise ValueError('分拣需要真实/仿真相机帧，导入图片不能作为当前工件依据')
    stamp = result.get('captured_at')
    if type(stamp) not in (int, float) or not math.isfinite(stamp) or not 0 <= now-stamp <= 1.5 or snapshot.get('stale'):
        raise ValueError('分拣视觉帧已过期，请重新采集A区')
    key = result.get('source_key')
    identity=camera_identity(snapshot)
    if identity is None:raise ValueError('分拣相机会话或设备身份与观测来源不匹配')
    image = result.get('image') or {}
    size = [image.get('width'), image.get('height')]
    errors = calibration.get('check_errors_mm')
    if (not result.get('calibrated') or not key or calibration.get('source_key') != key
            or calibration.get('image_size') != size or not errors or len(errors) != 2
            or any(type(e) not in (int, float) or not math.isfinite(e) or not 0 <= e <= 10 for e in errors)
            or not calibration.get('matrix') or not snapshot.get('instance_id')):
        raise ValueError('分拣缺少当前相机会话/分辨率的有效平面标定与独立复核')
    observations = result.get('objects') or []
    if len(observations) > 50:
        raise ValueError('分拣观测数量超限')
    bound = {}; used = set()
    for piece in pieces:
        if piece['task']['slot'] >= 0:
            continue
        matches = []
        tolerance = min(20., min(piece['size'][:2]) / 4)
        for obj in observations:
            mapped = obj.get('mapped') or {}
            xyz = mapped.get('xyz_mm')
            if (obj.get('status') != 'observed' or mapped.get('coordinate_space') != 'base_xy_planar_estimate'
                    or not isinstance(xyz, list) or len(xyz) != 3
                    or any(type(v) not in (int, float) or not math.isfinite(v) for v in xyz)):
                continue
            if math.dist(xyz[:2], piece['xyz'][:2]) <= tolerance and abs(xyz[2]-piece['xyz'][2]) <= 3:
                matches.append(obj)
        if len(matches) != 1:
            raise ValueError('工件 '+piece['id']+' 视觉位置绑定缺失或歧义，请核对视野/标定/高度')
        obj = matches[0]; track = obj.get('id')
        if not isinstance(track, str) or not track or track in used or obj.get('color') not in ('red', 'green', 'blue'):
            raise ValueError('工件视觉跟踪ID重复或检测颜色不在红绿蓝中')
        used.add(track)
        bound[piece['id']] = {'track_id': track, 'color': obj['color'], 'rgb': obj.get('rgb'),
                              'xyz_mm': list(obj['mapped']['xyz_mm'])}
    return {'instance_id': snapshot['instance_id'], 'source_key': key, 'source': result['source'], 'camera_identity':identity,
            'frame_seq': result.get('frame_seq'), 'captured_at': stamp,
            'config': copy.deepcopy(config), 'calibration': copy.deepcopy(calibration), 'bindings': bound}


def evidence_current(evidence, snapshot, *, allow_view_change=False):
    """Only an active, start-validated plan may use its frozen camera view.

    Identity/config/calibration remain mandatory even when the robot moves its
    own mounted camera. No new coordinates are inferred from that changed view.
    """
    return (snapshot.get('instance_id') == evidence['instance_id']
            and camera_identity(snapshot) is not None
            and camera_identity(snapshot) == evidence['camera_identity']
            and snapshot.get('config') == evidence['config']
            and snapshot.get('calibration') == evidence['calibration']
            and (allow_view_change or (snapshot.get('result') or {}).get('source_key') == evidence['source_key']))


def sorting_targets(pieces, zone, colors):
    """Pack bands with actual footprint and a 10mm separation; never overlap."""
    keys = sorted(set(colors.values()))
    if not keys:
        raise ValueError('没有待取的A区工件，请先回A或重新布置')
    width, height = zone['size'][:2]; usable = width-30; band = usable/len(keys)
    output = []
    for gi, key in enumerate(keys):
        selected = [p for p in pieces if colors.get(p['id']) == key]
        sx = max(p['size'][0] for p in selected); sy = max(p['size'][1] for p in selected)
        columns = math.floor((band+10)/(sx+10))
        if columns < 1:
            raise ValueError('B区颜色分带宽度不足，请减少尺寸或扩大料区')
        rows = math.ceil(len(selected)/columns)
        if rows*sy+(rows-1)*10 > height-30:
            raise ValueError('B区容量不足，请分批执行')
        for index, piece in enumerate(selected):
            xyz = [zone['xyz'][0]-usable/2+band*(gi+.5)+(index%columns-(columns-1)/2)*(sx+10),
                   zone['xyz'][1]+(index//columns-(rows-1)/2)*(sy+10),
                   zone['xyz'][2]+zone['size'][2]/2+piece['size'][2]/2]
            output.append((piece, xyz, piece['task']['home']))
    return output
