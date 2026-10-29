"""CPU-only orthographic inspection of the official STL; no browser or renderer."""
import argparse
import json
from pathlib import Path

import cv2
import numpy as np


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--colors', type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1] / 'web/public/models/elfin5'
    spec = json.loads((root / 'robot.json').read_text(encoding='utf-8'))
    colors = json.loads(args.colors.read_text(encoding='utf-8')) if args.colors else {}
    links = [(name, link) for name, link in spec['links'].items() if link.get('visual')]
    width, height = 340, 270
    sheet = np.full((height * len(links), width * 6, 3), (24, 27, 29), dtype=np.uint8)
    dtype = np.dtype([('normal', '<f4', (3,)), ('vertices', '<f4', (3,3)), ('attribute', '<u2')])
    for row, (name, link) in enumerate(links):
        data = (root / link['visual']['mesh']).read_bytes()
        triangles = np.frombuffer(data, dtype=dtype, offset=84)['vertices'].astype(float)
        normals = np.cross(triangles[:,1] - triangles[:,0], triangles[:,2] - triangles[:,0])
        length = np.linalg.norm(normals,axis=1)
        normals /= np.maximum(length[:,None],1e-20)
        center = (triangles.reshape(-1,3).min(axis=0) + triangles.reshape(-1,3).max(axis=0))/2
        shaded = np.full(len(triangles), False)
        if name in colors:
            shaded[np.array(colors[name],dtype=int)] = True
        for col, (axis, sign) in enumerate([(0,1),(0,-1),(1,1),(1,-1),(2,1),(2,-1)]):
            u,v = [i for i in range(3) if i != axis]
            points = triangles[:,:,[u,v]] - center[[u,v]]
            span = np.ptp(points.reshape(-1,2),axis=0)
            scale = min((width-35)/max(span[0],1e-10),(height-55)/max(span[1],1e-10))
            points *= [sign * scale,-scale]
            points += [width/2, height/2+12]
            depths = triangles[:,:,axis].mean(axis=1)*sign
            panel = sheet[row*height:(row+1)*height,col*width:(col+1)*width]
            for idx in depths.argsort():
                light = .46 + .45 * max(0,float(normals[idx,axis]*sign))
                base = 40 if shaded[idx] else 220
                color = int(base*light)
                cv2.fillConvexPoly(panel,points[idx].astype(np.int32),(color,color,color),lineType=cv2.LINE_8)
            cv2.putText(panel,f'{name} {"+" if sign > 0 else "-"}{"xyz"[axis]}',
                        (10,22),cv2.FONT_HERSHEY_SIMPLEX,.47,(220,220,220),1,cv2.LINE_AA)
    args.output.parent.mkdir(parents=True,exist_ok=True)
    cv2.imwrite(str(args.output),sheet)
    print(args.output)


if __name__ == '__main__':
    main()
