"""Cached source-mesh support queries, not centroid-centred bounding boxes.

For a plane, the minimum over a triangular mesh is attained at a vertex.
Read and deduplicate once; runtime queries only project the cached vertices.
"""
from functools import lru_cache
import math
import struct

try:
    import numpy as np
except ImportError:
    np = None


@lru_cache(maxsize=96)
def mesh_vertices(path):
    try:
        raw=path.read_bytes()
        count=struct.unpack_from('<I',raw,80)[0]
        if count<=0 or len(raw)!=84+50*count:
            return None
        if np is not None:
            points=np.ndarray((count,3,3),dtype='<f4',buffer=raw,offset=96,
                              strides=(50,12,4)).reshape(-1,3)
            if not np.isfinite(points).all():return None
            points=np.unique(points,axis=0)
            points.setflags(write=False)
            return points
        points=tuple(set(struct.unpack_from('<3f',raw,84+50*i+12+12*j)
                         for i in range(count) for j in range(3)))
        return points if all(math.isfinite(v) for p in points for v in p) else None
    except (OSError,ValueError,struct.error):
        return None


def lowest_vertex(points,transform):
    normal=transform[2][:3]
    if np is not None and isinstance(points,np.ndarray):
        # einsum avoids dispatching a tiny matrix query to a BLAS thread pool.
        heights=np.einsum('ij,j->i',points,normal,optimize=False)
        point=points[int(heights.argmin())]
    else:
        point=min(points,key=lambda p:sum(a*b for a,b in zip(p,normal)))
    world=[sum(transform[i][j]*float(point[j]) for j in range(3))+transform[i][3]
           for i in range(3)]
    return world[2]*1000,[v*1000 for v in world]
