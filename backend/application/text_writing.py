"""Finite glyph contours on the ground; input is geometry, not executable code."""
import math

def checked_text(payload,mode):
    if mode!='sim':raise ValueError('文字书写仅在仿真开放')
    text=payload.get('text')
    if not isinstance(text,str) or not 1<=len(text.strip())<=5 or any(ord(c)<32 or ord(c)==127 for c in text):raise ValueError('请输入1～5个字符')
    strokes=payload.get('strokes')
    if not isinstance(strokes,list) or not 1<=len(strokes)<=256:raise ValueError('文字笔画无效')
    total=0
    for stroke in strokes:
        if not isinstance(stroke,list) or len(stroke)<2:raise ValueError('文字笔画至少需要两个点')
        total+=len(stroke)
        if total>1800:raise ValueError('文字轮廓过于复杂，请减少字符')
        for p in stroke:
            if not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>5 for v in p):raise ValueError('文字轮廓坐标无效')
    if total>1800:raise ValueError('文字轮廓过于复杂，请减少字符')
    size=payload.get('font_size_mm')
    if type(size) not in (int,float) or not math.isfinite(size) or not 20<=size<=180:raise ValueError('字号需要20～180毫米')
    height=payload.get('writing_z_mm',1.)
    if type(height) not in (int,float) or not math.isfinite(height) or not 1<=height<=800:raise ValueError('书写高度需要1～800毫米')
    return dict(text=text.strip(),strokes=strokes,font_size_mm=float(size),writing_z_mm=float(height))

def paths(config):
    x,y=config['center'][:2];size=config['font_size_mm']
    # Human stands at negative X: glyph right is -Y, glyph top is +X.
    return [[[x+b*size,y-a*size,config.get('writing_z_mm',1.),180.,0.,0.] for a,b in stroke] for stroke in config['strokes']]

def sequence(config):
    start=config['center'][:];rows=[start];flags=[False];ids=[None]
    plane=config.get('writing_z_mm',1.);hover=plane+15.
    for index,stroke in enumerate(paths(config)):
        lift=stroke[0][:];lift[2]=hover
        rows.extend([lift,stroke[0]]);flags.extend([False,False]);ids.extend([None,None])
        for p in stroke[1:]:rows.append(p);flags.append(True);ids.append(index)
        lift=stroke[-1][:];lift[2]=hover
        rows.append(lift);flags.append(False);ids.append(None)
    # Lift vertically from the final stroke before returning above the text.
    # Respect the caller's checked box; the UI reserves 60mm clearance.
    ceiling=config.get('range_center_mm',start[:3])[2]+config['range_size_mm'][2]/2
    finish=rows[-1][:];finish[2]=max(hover,min(plane+60.,ceiling-2.))
    rows.append(finish);flags.append(False);ids.append(None)
    home=start[:];home[2]=finish[2]
    rows.append(home);flags.append(False);ids.append(None)
    return rows,flags,ids
