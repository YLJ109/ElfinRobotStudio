"""Fetch pinned, licensed Robotiq originals and assemble offline tool-library GLBs.

Run with the project's Python: tools/build_tool_assets.py --download
Without --download the build is entirely offline. All generated assets and source
evidence stay in web/public/tools. This script never contacts a controller.
"""
from __future__ import annotations

import argparse
import ast
import concurrent.futures
import hashlib
import json
import math
import operator
import re
import struct
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.request import Request, urlopen

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'web/public/tools'
REPO = 'ros-industrial-attic/robotiq'
COMMIT = '45196f6558fe8ba9d89bc8a105396c68c3e7e892'
SOURCE = OUT / 'source' / 'robotiq'
TOOLS = [
    ('robotiq-2f85', 'Robotiq 2F-85', 'robotiq_2f_85_gripper_visualization'),
    ('robotiq-2f140', 'Robotiq 2F-140', 'robotiq_2f_140_gripper_visualization'),
    ('robotiq-3f', 'Robotiq 3F', 'robotiq_3f_gripper_visualization'),
]
SURFACES = {
    'silver': {'color': [.62, .67, .72, 1], 'metalness': .65, 'roughness': .28},
    'black-metal': {'color': [.012, .016, .022, 1], 'metalness': .5, 'roughness': .34},
    'rubber': {'color': [.009, .012, .016, 1], 'metalness': .02, 'roughness': .8},
}
EXTRA_SOURCES = {
    'franka': {
        'repository': 'frankarobotics/franka_ros', 'commit': 'ddd2fffd9de44b02ad15b4bbb2bfa2cec4d60d98',
        'license': 'Apache-2.0', 'files': ['LICENSE', 'NOTICE', 'franka_description/package.xml',
            'franka_description/meshes/visual/hand.dae', 'franka_description/meshes/visual/finger.dae',
            'franka_description/robots/common/franka_hand.xacro', 'franka_description/robots/common/hand.urdf.xacro'],
    },
    'schunk': {
        'repository': 'ipa320/schunk_modular_robotics', 'commit': 'b2d73bdd333eceda0e91b4f8d4e8881433dc243a',
        'license': 'Apache-2.0', 'files': ['LICENSE', 'schunk_description/package.xml',
            'schunk_description/urdf/sdh/sdh.urdf.xacro', 'schunk_description/urdf/materials.urdf.xacro',
            'schunk_description/meshes/sdh/palm.stl', 'schunk_description/meshes/sdh/knuckle.stl',
            'schunk_description/meshes/sdh/finger.stl', 'schunk_description/meshes/sdh/finger_tip.stl'],
    },
    'wsg': {
        'repository': 'RobotLocomotion/models', 'commit': 'f85076b4d95a8bdfec70153015e723e92312e4e2',
        'license': 'BSD-3-Clause', 'files': ['wsg_50_description/LICENSE', 'wsg_50_description/README.md',
            'wsg_50_description/meshes/wsg_body.gltf', 'wsg_50_description/meshes/wsg_body.bin',
            'wsg_50_description/meshes/finger_with_tip.gltf', 'wsg_50_description/meshes/finger_with_tip.bin',
            'wsg_50_description/sdf/schunk_wsg_50_with_tip.sdf'],
    },
}


def fetch(url: str) -> bytes:
    with urlopen(Request(url, headers={'User-Agent': 'ElfinRobotStudio-tool-assets'}), timeout=45) as response:
        return response.read()


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def download_sources() -> None:
    tree = json.loads(fetch(f'https://api.github.com/repos/{REPO}/git/trees/{COMMIT}?recursive=1'))
    if tree.get('truncated') or tree.get('sha') != COMMIT:
        raise ValueError('Expected a complete pinned source tree')
    packages = [item[2] for item in TOOLS]
    paths = ['LICENSE']
    for item in tree['tree']:
        path = item['path']
        if item['type'] != 'blob':
            continue
        for package in packages:
            tail = path.removeprefix(package + '/')
            if path.startswith(package + '/') and (tail == 'package.xml' or tail.startswith(('urdf/', 'cfg/')) or
                    ('/visual/' in tail and tail.lower().endswith(('.stl', '.dae', '.png', '.jpg')))):
                paths.append(path)
                break

    def download(path):
        url = f'https://raw.githubusercontent.com/{REPO}/{COMMIT}/{path}'
        data = fetch(url)
        target = SOURCE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)  # Network download, byte-identical upstream original.
        return {'path': path, 'url': url, 'sha256': digest(data), 'bytes': len(data)}

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        files = sorted(pool.map(download, paths), key=lambda item: item['path'])
    write_json(SOURCE / 'manifest.json', {
        'repository': f'https://github.com/{REPO}', 'commit': COMMIT,
        'verified_date': '2026-10-26', 'files': files,
        'license_note': 'Root LICENSE is BSD-2-Clause; package.xml declares BSD. Keep individual embedded notices too.',
        'upstream_review': {
            'repository': 'https://github.com/robotiq/robotiq-ros',
            'commit': 'be8aa38abb2f84e85adb9d6862092b9dfc0124c5',
            'license': 'BSD-3-Clause',
            'note': 'Reviewed current official repository; these meshes instead use the pinned coherent ROS-Industrial set above.',
        },
    })
    print(json.dumps({'downloaded_files': len(files), 'original_bytes': sum(f['bytes'] for f in files)}))


def download_extra_sources():
    for key, source in EXTRA_SOURCES.items():
        destination = OUT / 'source' / key
        def download(path):
            url = f"https://raw.githubusercontent.com/{source['repository']}/{source['commit']}/{path}"
            data = fetch(url)
            target = destination / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            return {'path': path, 'url': url, 'sha256': digest(data), 'bytes': len(data)}
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            files = sorted(pool.map(download, source['files']), key=lambda item: item['path'])
        write_json(destination / 'manifest.json', {'repository': f"https://github.com/{source['repository']}",
            'commit': source['commit'], 'license': source['license'], 'verified_date': '2026-10-26', 'files': files})
        print(json.dumps({'source': key, 'downloaded': len(files), 'bytes': sum(item['bytes'] for item in files)}), flush=True)


def verify_sources():
    manifest = json.loads((SOURCE / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['commit'] != COMMIT:
        raise ValueError('Unexpected source commit')
    for entry in manifest['files']:
        data = (SOURCE / entry['path']).read_bytes()
        if len(data) != entry['bytes'] or digest(data) != entry['sha256']:
            raise ValueError(f"Original changed: {entry['path']}")
    return manifest


def local_tag(element):
    return element.tag.split('}')[-1]


def numeric_expression(text, scope):
    """Only arithmetic and explicit xacro parameter names; never eval source code."""
    operators = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
                 ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}

    def read(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str)):
            return node.value
        if isinstance(node, ast.Name) and node.id in scope:
            return scope[node.id]
        if isinstance(node, ast.BinOp) and type(node.op) in operators:
            return operators[type(node.op)](read(node.left), read(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in operators:
            return operators[type(node.op)](read(node.operand))
        raise ValueError(f'Unsupported source expression: {text}')

    return read(ast.parse(text, mode='eval').body)


def expanded_2f(package, stroke):
    directory = SOURCE / package / 'urdf'
    macros = {}
    for path in sorted(directory.glob('*.xacro')):
        for element in ET.parse(path).getroot():
            if local_tag(element) == 'macro':
                macros[element.attrib['name']] = element

    def substitute(text, scope):
        return re.sub(r'\$\{([^}]+)\}', lambda match: str(numeric_expression(match[1], scope)), text)

    def expand(element, scope, depth=0):
        if depth > 30:
            raise ValueError('Recursive xacro depth exceeded')
        name = local_tag(element)
        attributes = {key: substitute(value, scope) for key, value in element.attrib.items()}
        if name in macros and '}' in element.tag:
            macro = macros[name]
            arguments = {}
            for key in macro.attrib['params'].split():
                value = attributes[key]
                try:
                    arguments[key] = int(value)
                except ValueError:
                    try:
                        arguments[key] = float(value)
                    except ValueError:
                        arguments[key] = value
            result = []
            for child in macro:
                result.extend(expand(child, {**scope, **arguments}, depth + 1))
            return result
        if name in ('include', 'macro'):
            return []
        result = ET.Element(name, attributes)
        result.text = substitute(element.text, scope) if element.text else None
        for child in element:
            result.extend(expand(child, scope, depth + 1))
        return [result]

    root = ET.Element('robot', {'name': f'robotiq_2f_{stroke}'})
    macro = macros[f'robotiq_arg2f_{stroke}']
    for child in macro:
        root.extend(expand(child, {'prefix': '', 'pi': math.pi}))
    return root


def numbers(text, default):
    return np.array([float(value) for value in text.split()] if text is not None else default, dtype=np.float64)


def origin_matrix(origin):
    matrix = np.eye(4)
    if origin is None:
        return matrix
    rx, ry, rz = numbers(origin.get('rpy'), [0, 0, 0])
    sx, sy, sz = np.sin([rx, ry, rz])
    cx, cy, cz = np.cos([rx, ry, rz])
    matrix[:3, :3] = [[cz*cy, cz*sy*sx-sz*cx, cz*sy*cx+sz*sx],
                      [sz*cy, sz*sy*sx+cz*cx, sz*sy*cx-cz*sx],
                      [-sy, cy*sx, cy*cx]]
    matrix[:3, 3] = numbers(origin.get('xyz'), [0, 0, 0])
    return matrix


def stl_triangles(path):
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError(f'Truncated STL: {path}')
    count = struct.unpack_from('<I', data, 80)[0]
    if len(data) != 84 + count * 50:
        raise ValueError(f'Only valid binary STL is supported: {path}')
    dtype = np.dtype([('normal', '<f4', 3), ('vertices', '<f4', (3, 3)), ('attribute', '<u2')])
    return np.frombuffer(data, dtype=dtype, offset=84)['vertices'].astype(np.float64)


def dae_triangles(path, geometry_id=None, identity_nodes=False, geometry_nodes_only=False):
    """Read triangles in the original mesh coordinate system used by its URDF.

    These 2F-85 exports have no node transforms or unit declarations; URDF's
    explicit 0.001 scale is authoritative. Do not infer an extra axis rotation
    from the inconsistent Y_UP metadata on the outer-knuckle export.
    """
    root = ET.parse(path).getroot()
    ns = {'c': 'http://www.collada.org/2005/11/COLLADASchema'}
    nodes = root.findall('.//c:node', ns)
    if geometry_nodes_only:
        # Blender's camera/light transforms are not mesh transforms. The pinned
        # articulated 3F geometry instances are identity; reject any other pose.
        nodes = [node for node in nodes if node.find('c:instance_geometry', ns) is not None]
    matrices = [e for node in nodes for e in node.findall('c:matrix', ns)]
    if ((matrices and not (identity_nodes and all(np.allclose(np.fromstring(e.text, sep=' ').reshape(4, 4), np.eye(4)) for e in matrices))) or
            any(node.findall('c:translate', ns) or node.findall('c:rotate', ns) or node.findall('c:scale', ns) for node in nodes)):
        raise ValueError(f'Transformed DAE needs an explicit importer: {path}')
    parts = []
    for geometry in root.findall('c:library_geometries/c:geometry', ns):
        if geometry_id is not None and geometry.get('id') != geometry_id:
            continue
        mesh = geometry.find('c:mesh', ns)
        sources = {}
        for source in mesh.findall('c:source', ns):
            array = source.find('c:float_array', ns)
            accessor = source.find('c:technique_common/c:accessor', ns)
            if array is None or accessor is None:
                continue
            stride, offset = int(accessor.get('stride', '1')), int(accessor.get('offset', '0'))
            values = np.fromstring(array.text or '', sep=' ')
            count = int(accessor.get('count'))
            sources[source.get('id')] = values[offset:offset + stride * count].reshape(count, stride)
        vertices = {vertex.get('id'): vertex.find('c:input[@semantic="POSITION"]', ns).get('source')[1:]
                    for vertex in mesh.findall('c:vertices', ns)}
        for triangle in mesh.findall('c:triangles', ns):
            inputs = triangle.findall('c:input', ns)
            width = max(int(item.get('offset', '0')) for item in inputs) + 1
            vertex = next(item for item in inputs if item.get('semantic') == 'VERTEX')
            indices = np.fromstring(triangle.findtext('c:p', '', ns), sep=' ', dtype=np.int64).reshape(-1, width)
            positions = sources[vertices[vertex.get('source')[1:]]]
            part = positions[indices[:, int(vertex.get('offset', '0'))], :3].reshape(-1, 3, 3)
            if len(part) != int(triangle.get('count')):
                raise ValueError(f'Triangle count mismatch: {path}')
            parts.append(part)
    if not parts:
        raise ValueError(f'No actual source triangles: {path}')
    return np.concatenate(parts)


def box_triangles(size):
    # The real URDF models the two rubber contact pads as boxes, not the tool.
    vertices = np.array([[x, y, z] for x in [-1, 1] for y in [-1, 1] for z in [-1, 1]]) * size / 2
    faces = [[0, 2, 3], [0, 3, 1], [4, 5, 7], [4, 7, 6], [0, 1, 5], [0, 5, 4],
             [2, 6, 7], [2, 7, 3], [0, 4, 6], [0, 6, 2], [1, 3, 7], [1, 7, 5]]
    return vertices[np.array(faces)]


def assemble(urdf, package, tool_id, source_directory=SOURCE, normalization=None):
    links = {link.get('name'): link for link in urdf.findall('link')}
    transforms, parents = {}, {}
    for joint in urdf.findall('joint'):
        child, parent = joint.find('child').get('link'), joint.find('parent').get('link')
        if child in parents:
            raise ValueError('Link with multiple parents')
        parents[child] = (parent, origin_matrix(joint.find('origin')))

    def transform(name, visiting=frozenset()):
        if name in visiting:
            raise ValueError('Cyclic source assembly')
        if name not in transforms:
            transforms[name] = (transform(parents[name][0], visiting | {name}) @ parents[name][1]
                                if name in parents else np.eye(4))
        return transforms[name]

    supplied_normalization = normalization is not None
    normalization = np.eye(4) if normalization is None else normalization
    if tool_id == 'robotiq-3f' and not supplied_normalization:
        # The upstream fixed-open STL assembly uses a shared CAD datum. Its
        # palm mounting plane is Z=-161.125 mm. Move only that datum to zero.
        normalization[2, 3] = .161125
    parts = []
    for name, link in links.items():
        for visual in link.findall('visual'):
            geometry = visual.find('geometry')
            mesh, box = geometry.find('mesh'), geometry.find('box')
            original = None
            if mesh is not None:
                original = mesh.get('filename').removeprefix('package://')
                if not original.startswith(package + '/') or '..' in original.split('/'):
                    raise ValueError('Unexpected source asset path')
                path = source_directory / original
                triangles = dae_triangles(path, identity_nodes=True, geometry_nodes_only=True) if path.suffix.lower() == '.dae' else stl_triangles(path)
                triangles *= numbers(mesh.get('scale'), [1, 1, 1])
            elif box is not None:
                triangles = box_triangles(numbers(box.get('size'), []))
            else:
                raise ValueError('Unimplemented upstream visual geometry')
            matrix = normalization @ transform(name) @ origin_matrix(visual.find('origin'))
            triangles = triangles @ matrix[:3, :3].T + matrix[:3, 3]
            color_element = visual.find('material/color')
            color = numbers(color_element.get('rgba') if color_element is not None else None, [.16, .18, .2, 1]).tolist()
            if tool_id == 'robotiq-3f':
                # Replace the source RViz diagnostic green with neutral materials;
                # geometry and assembly are unmodified. Explicitly recorded below.
                color = [.28, .30, .33, 1] if name == 'palm' else [.12, .13, .15, 1]
            surface = 'rubber' if name.endswith('inner_finger_pad') else 'silver'
            if 'inner_knuckle' in name or (tool_id == 'robotiq-3f' and name != 'palm'):
                surface = 'black-metal'
            if tool_id == 'schunk-sdh' and name.endswith(('13_link', '23_link', '33_link')):
                surface = 'rubber'
            parts.append({'name': name, 'link': name, 'triangles': triangles, 'color': SURFACES[surface]['color'], 'source': original, 'surface': surface})
    return parts


def verify_extra_source(key):
    directory = OUT / 'source' / key
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest['commit'] != EXTRA_SOURCES[key]['commit']:
        raise ValueError('Unexpected additional-source commit')
    for item in manifest['files']:
        data = (directory / item['path']).read_bytes()
        if digest(data) != item['sha256'] or len(data) != item['bytes']:
            raise ValueError(f"Original changed: {key}/{item['path']}")
    return directory, manifest


def franka_parts(directory):
    ns = {'c': 'http://www.collada.org/2005/11/COLLADASchema'}
    result = []
    for link, filename, xyz, yaw in [('hand', 'hand.dae', [0, 0, 0], 0),
            ('leftfinger', 'finger.dae', [0, .04, .0584], 0),
            ('rightfinger', 'finger.dae', [0, -.04, .0584], math.pi)]:
        original = 'franka_description/meshes/visual/' + filename
        path = directory / original
        root = ET.parse(path).getroot()
        matrix = origin_matrix(ET.Element('origin', {'xyz': ' '.join(map(str, xyz)), 'rpy': f'0 0 {yaw}'}))
        effects = {e.get('id'): numbers(e.findtext('.//c:diffuse/c:color', None, ns), [1, 1, 1, 1])
                   for e in root.findall('c:library_effects/c:effect', ns)}
        materials = {e.get('id'): e.find('c:instance_effect', ns).get('url')[1:]
                     for e in root.findall('c:library_materials/c:material', ns)}
        for geometry in root.findall('c:library_geometries/c:geometry', ns):
            triangles = dae_triangles(path, geometry.get('id'), identity_nodes=True)
            material = geometry.find('c:mesh/c:triangles', ns).get('material')
            color = effects.get(materials.get(material), np.array([1, 1, 1, 1]))
            dark = np.mean(color[:3]) < .5
            surface = 'rubber' if dark and link != 'hand' else ('black-metal' if dark else 'silver')
            result.append({'name': link + '_' + geometry.get('id'), 'link': link, 'source': original, 'surface': surface,
                'color': SURFACES[surface]['color'], 'triangles': triangles @ matrix[:3, :3].T + matrix[:3, 3]})
    return result


def sdh_robot(directory):
    original = directory / 'schunk_description/urdf/sdh/sdh.urdf.xacro'
    text = original.read_text(encoding='utf-8').replace('${name}', 'sdh').replace('${parent}', 'mount')
    macro = next(e for e in ET.fromstring(text) if local_tag(e) == 'macro')
    robot = ET.Element('robot')
    robot.append(ET.Element('link', {'name': 'mount'}))
    for element in macro:
        if local_tag(element) in ('link', 'joint'):
            for nested in list(element):
                if local_tag(nested) == 'insert_block': element.remove(nested)
            robot.append(element)
    return robot


def sdh_parts(directory):
    return assemble(sdh_robot(directory), 'schunk_description', 'schunk-sdh', directory)


def gltf_triangles(path):
    document = json.loads(path.read_text(encoding='utf-8'))
    buffers = []
    for buffer in document['buffers']:
        uri = buffer['uri']
        if '/' in uri or '\\' in uri or ':' in uri or '..' in uri:
            raise ValueError('Unexpected glTF sidecar')
        buffers.append((path.parent / uri).read_bytes())
    def read_accessor(index):
        accessor = document['accessors'][index]
        view = document['bufferViews'][accessor['bufferView']]
        dtype = np.dtype({5126: '<f4', 5125: '<u4', 5123: '<u2', 5121: 'u1'}[accessor['componentType']])
        size = {'VEC3': 3, 'SCALAR': 1}[accessor['type']]
        return np.ndarray((accessor['count'], size), dtype=dtype, buffer=buffers[view['buffer']],
            offset=view.get('byteOffset', 0) + accessor.get('byteOffset', 0),
            strides=(view.get('byteStride', size * dtype.itemsize), dtype.itemsize)).copy()
    if any(set(node) - {'name', 'mesh'} for node in document['nodes']):
        raise ValueError('Source glTF has unhandled transforms')
    parts = []
    for mesh in document['meshes']:
        for primitive in mesh['primitives']:
            if primitive.get('mode', 4) != 4: raise ValueError('Source mesh is not triangles')
            positions = read_accessor(primitive['attributes']['POSITION'])
            indices = read_accessor(primitive['indices']).reshape(-1, 3)
            parts.append(positions[indices].astype(np.float64))
    return np.concatenate(parts)


def wsg_parts(directory):
    sdf = ET.parse(directory / 'wsg_50_description/sdf/schunk_wsg_50_with_tip.sdf').getroot()
    parts = []
    for link in sdf.findall('model/link'):
        visual = link.find('visual')
        uri = visual.findtext('geometry/mesh/uri').removeprefix('package://drake_models/')
        triangles = gltf_triangles(directory / uri)
        # glTF is Y-up, while SDFormat/Drake's model coordinates are Z-up.
        # Apply that loader convention before the SDF link pose, or fingers
        # would float above the body and the tool TCP would be inconsistent.
        triangles = triangles @ np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]]).T
        pose = numbers(link.findtext('pose'), [0] * 6)
        if link.get('name') == 'left_finger': pose[0] -= .03
        if link.get('name') == 'right_finger': pose[0] += .03
        matrix = origin_matrix(ET.Element('origin', {'xyz': ' '.join(map(str, pose[:3])), 'rpy': ' '.join(map(str, pose[3:]))}))
        triangles = triangles @ matrix[:3, :3].T + matrix[:3, 3]
        surface = 'silver' if link.get('name') == 'body' else 'black-metal'
        parts.append({'name': link.get('name'), 'link': link.get('name'), 'triangles': triangles, 'source': uri,
                      'surface': surface, 'color': SURFACES[surface]['color']})
    mount_y = min(part['triangles'][:, :, 1].min() for part in parts if part['name'] == 'body')
    rotation = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    for part in parts: part['triangles'] = part['triangles'] @ rotation.T - [0, 0, mount_y]
    return parts, [0, 0, round((.059 - mount_y) * 1000, 6)]


def animation_source(tool_id):
    """Source-verified rigid links. No asset writes/network; used by offline QA.

    q(c)=open+c*(closed-open) is a visual preset, not a physical controller's
    adaptive grasp law. A fixed reference TCP never follows the contact pads.
    """
    normal = np.eye(4)
    if tool_id.startswith('robotiq-2f'):
        stroke = 85 if tool_id.endswith('85') else 140
        package = f'robotiq_2f_{stroke}_gripper_visualization'
        robot = expanded_2f(package, stroke)
        parts = assemble(robot, package, tool_id)
        driver = .8 if stroke == 85 else .7
        closed = {'finger_joint': driver}
        for joint in robot.findall('joint'):
            mimic = joint.find('mimic')
            if mimic is not None:
                closed[joint.get('name')] = driver * float(mimic.get('multiplier', '1')) + float(mimic.get('offset', '0'))
        opened = {}
        note = 'Source URDF driver and mimic joints; mimic signs preserved even where upstream mimic limits are inconsistent. No adaptive contact dynamics.'
    elif tool_id == 'robotiq-3f':
        package = 'robotiq_3f_gripper_visualization'
        robot = ET.parse(SOURCE / package / 'cfg/robotiq-3f-gripper_articulated.urdf').getroot()
        # The articulated export uses a different CAD frame from GRIPPER_OPEN.
        # Its +Y is tool-forward. Preserve the old flange/TCP convention by
        # rotating +Y to +Z and placing the rear palm face on flange Z=0.
        normal[:3, :3] = [[1, 0, 0], [0, 0, -1], [0, 1, 0]]
        for mesh in robot.findall('.//visual/geometry/mesh'):
            mesh.set('filename', mesh.get('filename').rsplit('.', 1)[0] + '.dae')
        parts = assemble(robot, package, tool_id, normalization=normal)
        shift = min(p['triangles'][:, :, 2].min() for p in parts if p['name'] == 'palm')
        normal[2, 3] = -shift
        for part in parts:
            part['triangles'][:, :, 2] -= shift
        opened = {}
        closed = {j.get('name'): {1: .65, 2: .4, 3: .15}[int(j.get('name')[-1])]
                  for j in robot.findall('joint') if '_joint_' in j.get('name')}
        note = 'Pinned articulated DAE links, not merged open STL. Basic symmetric curl preset; spread joints fixed. Upstream link_2 visual offset is explicitly marked suspect; no calibrated adaptive grasp.'
    elif tool_id == 'schunk-sdh':
        directory, _ = verify_extra_source('schunk')
        robot = sdh_robot(directory)
        parts = sdh_parts(directory)
        opened = {}
        closed = {j.get('name'): (.08 if j.get('name').endswith(('12_joint', '22_joint', 'thumb_2_joint')) else .10)
                  for j in robot.findall('joint') if j.get('name').endswith(('12_joint', '22_joint', 'thumb_2_joint', '13_joint', '23_joint', 'thumb_3_joint'))}
        note = 'Source revolute finger axes; modest symmetric curl preset, knuckle spread fixed at zero. Not a full-stroke physical grasp or force/contact simulation.'
    else:
        robot = ET.Element('robot')
        opened, closed = {}, {}
        if tool_id == 'franka-panda-hand':
            directory, _ = verify_extra_source('franka')
            parts = franka_parts(directory)
            root, names, axes, origin, q0, q1 = 'hand', ['leftfinger', 'rightfinger'], ['0 1 0', '0 -1 0'], '0 0 .0584', .04, 0
            note = 'Source hand xacro prismatic joints 0..40 mm each; rigid multi-material fingers move together.'
        elif tool_id == 'schunk-wsg50':
            directory, _ = verify_extra_source('wsg')
            parts, _ = wsg_parts(directory)
            root, names, axes, origin, q0, q1 = 'body', ['left_finger', 'right_finger'], ['-1 0 0', '1 0 0'], '0 0 0', .03, 0
            note = 'Source SDF prismatic sliders and existing +/-30 mm open offsets. Preset closes only the displayed 60 mm stroke (source permits 110 mm); drawing-derived mesh, no hardware calibration.'
        else:
            raise ValueError('Unknown animation tool')
        ET.SubElement(robot, 'link', name=root)
        for name, axis in zip(names, axes):
            ET.SubElement(robot, 'link', name=name)
            joint = ET.SubElement(robot, 'joint', name=name + '_slide', type='prismatic')
            ET.SubElement(joint, 'parent', link=root); ET.SubElement(joint, 'child', link=name)
            ET.SubElement(joint, 'origin', xyz=origin); ET.SubElement(joint, 'axis', xyz=axis)
            opened[joint.get('name')], closed[joint.get('name')] = q0, q1

    joints = {j.find('child').get('link'): j for j in robot.findall('joint')}
    links, visited = [], set()
    def add(name):
        if name in visited:
            return
        joint = joints.get(name)
        parent = joint.find('parent').get('link') if joint is not None else None
        if parent is not None:
            add(parent)
        matrix = origin_matrix(joint.find('origin')) if joint is not None else normal
        if parent is None and joint is not None:
            matrix = normal @ matrix
        kind = joint.get('type') if joint is not None else 'fixed'
        key = joint.get('name') if joint is not None else name
        links.append({'name': name, 'parent': parent, 'joint': key, 'type': kind,
            'origin': matrix.flatten(order='F').tolist(),
            'axis': numbers(joint.find('axis').get('xyz') if joint is not None and joint.find('axis') is not None else None, [1, 0, 0]).tolist(),
            'open': opened.get(key, 0), 'closed': closed.get(key, 0)})
        visited.add(name)
    for link in robot.findall('link'):
        add(link.get('name'))
    # 0.8 s quintic easing: peak 1.875/duration * 0.8 rad = 1.875 rad/s,
    # below the slowest source driver's 2 rad/s (Panda sliders <=0.094 m/s).
    rig = {'version': 1, 'simulation_only': True, 'duration_s': .8, 'tcp_policy': 'fixed-reference',
        'links': links, 'parts': [{'name': p['name'], 'link': p['link']} for p in parts],
        'limitations': note}
    rig['collision_bounds_mm'] = animation_bounds(parts, rig)
    return parts, rig


def animation_matrices(rig, closure):
    matrices = {}
    for link in rig['links']:
        matrix = np.array(link['origin']).reshape(4, 4, order='F').copy()
        q = link['open'] + closure * (link['closed'] - link['open'])
        axis = np.array(link['axis']); axis /= np.linalg.norm(axis)
        motion = np.eye(4)
        if link['type'] == 'prismatic':
            motion[:3, 3] = axis * q
        elif link['type'] == 'revolute':
            x, y, z = axis; c, s = math.cos(q), math.sin(q)
            cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
            motion[:3, :3] = c * np.eye(3) + (1-c) * np.outer(axis, axis) + s * cross
        matrix = matrix @ motion
        matrices[link['name']] = (matrices[link['parent']] @ matrix if link['parent'] is not None else matrix)
    return matrices


def animation_bounds(parts, rig):
    """Continuous sweep enclosure, NOT just endpoint or sampled bounds.

    Sample rigid local AABB corners at 128 intervals; expand by a proven global
    Lipschitz displacement bound / (2*128). Triangle-inequality chain radii
    bound every corner's derivative for revolute/prismatic q(c), including
    nested joints. Covers all c in [0,1] and reversals along this preset.
    """
    links = {link['name']: link for link in rig['links']}
    bind = animation_matrices(rig, 0)
    corners, lipschitz = [], 0.
    for part in parts:
        matrix = np.linalg.inv(bind[part['link']])
        local = part['triangles'].reshape(-1, 3) @ matrix[:3, :3].T + matrix[:3, 3]
        lo, hi = local.min(axis=0), local.max(axis=0)
        box = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
        corners.append((part['link'], box))
        radius, speed, link = np.linalg.norm(box, axis=1).max(), 0., links[part['link']]
        while True:
            delta = abs(link['closed'] - link['open'])
            if link['type'] == 'revolute':
                speed += delta * radius
            elif link['type'] == 'prismatic':
                speed += delta
                radius += max(abs(link['open']), abs(link['closed']))
            radius += np.linalg.norm(np.array(link['origin']).reshape(4, 4, order='F')[:3, 3])
            if link['parent'] is None:
                break
            link = links[link['parent']]
        lipschitz = max(lipschitz, speed)
    minimum, maximum = np.full(3, np.inf), np.full(3, -np.inf)
    for c in np.linspace(0, 1, 129):
        matrices = animation_matrices(rig, c)
        for name, box in corners:
            matrix = matrices[name]
            points = box @ matrix[:3, :3].T + matrix[:3, 3]
            minimum = np.minimum(minimum, points.min(axis=0)); maximum = np.maximum(maximum, points.max(axis=0))
    padding = lipschitz / 256 + .000002  # rounding + GLB float32 slack
    return {'min': (np.floor((minimum-padding)*1e6)/1000).tolist(),
            'max': (np.ceil((maximum+padding)*1e6)/1000).tolist()}


def build_extra_assets():
    catalog, report = [], []
    for key, tool_id, name in [('franka', 'franka-panda-hand', 'Franka Panda Hand'),
            ('schunk', 'schunk-sdh', 'Schunk SDH 三指手'), ('wsg', 'schunk-wsg50', 'Schunk WSG50')]:
        directory, manifest = verify_extra_source(key)
        if key == 'franka':
            parts, tcp = franka_parts(directory), [0, 0, 103.4]
            assembly = 'franka_description/robots/common/franka_hand.xacro'
            note = 'Official hand xacro default tcp_xyz=0 0 0.1034; static finger joints at +40 mm each.'
        elif key == 'schunk':
            parts, tcp = sdh_parts(directory), [0, 0, 150]
            assembly = 'schunk_description/urdf/sdh/sdh.urdf.xacro'
            note = 'Upstream grasp_joint Z=0.15 m; all hand joints fixed at zero.'
        else:
            parts, tcp = wsg_parts(directory)
            assembly = 'wsg_50_description/sdf/schunk_wsg_50_with_tip.sdf'
            note = 'Drawing-derived BSD mesh, not manufacturer CAD. Source +Y maps to +Z; rear body face is flange datum. TCP is mean SDF contact centre, approximate; static opening offset +/-30 mm.'
        parts, animation = animation_source(tool_id)
        vertices = np.concatenate([part['triangles'].reshape(-1, 3) for part in parts])
        bounds = {'min': np.round(vertices.min(axis=0)*1000, 6).tolist(), 'max': np.round(vertices.max(axis=0)*1000, 6).tolist()}
        source = f"https://github.com/{EXTRA_SOURCES[key]['repository']}/tree/{manifest['commit']}"
        license_path = 'wsg_50_description/LICENSE' if key == 'wsg' else 'LICENSE'
        entry = {'id': tool_id, 'name': name, 'type': 'parallel_gripper', 'asset': f'/tools/{tool_id}.glb',
            'source': source, 'license': manifest['license'], 'tcp_mm': tcp, 'bounds_mm': bounds,
            'default_rotation_deg': [0, 0, 0], 'units': 'm', 'commit': manifest['commit'],
            'license_file': f'/tools/source/{key}/{license_path}', 'source_manifest': f'/tools/source/{key}/manifest.json',
            'tcp_reference': note + ' Model reference only, not hardware calibration.', 'pose': 'fixed-open',
            'calibrated': False, 'grip_fingers': 3 if key == 'schunk' else 2,
            'assembly_source': assembly, 'preview': f'/tools/{tool_id}.png', 'skin': 'silver-black-pbr',
            'joint_data': 'Source-linked rigid-joint simulation; no controller IO or contact dynamics.',
            'animation': animation}
        metadata = {'source': source, 'commit': manifest['commit'], 'license': manifest['license'], 'units': 'm',
            'modifications': 'Static source assembly, mount-axis normalization, normals/indexing and silver/black PBR finish.',
            'tcp_reference': entry['tcp_reference'], 'pose': 'open bind pose; simulated rigid joints',
            'animation': animation}
        output = OUT / f'{tool_id}.glb'
        write_glb(output, parts, metadata)
        entry['sha256'] = digest(output.read_bytes())
        catalog.append(entry)
        report.append({'id': tool_id, 'bytes': output.stat().st_size, 'parts': len(parts),
            'triangles': sum(len(part['triangles']) for part in parts), 'sha256': entry['sha256'],
            'bounds_mm': bounds, 'tcp_mm': tcp, 'source_coordinate_notes': note,
            'mesh_sources': sorted({part['source'] for part in parts})})
        render_preview(OUT / f'{tool_id}.png', parts)
    return catalog, report


def write_glb(path, parts, metadata):
    binary = bytearray()
    document = {'asset': {'version': '2.0', 'generator': 'ElfinRobotStudio tools/build_tool_assets.py',
                          'extras': metadata}, 'scene': 0, 'scenes': [{'nodes': [0]}],
                'nodes': [{'name': path.stem if path is not None else 'offline-tool', 'children': [], 'extras': {'mount': 'flange', 'forward': '+Z'}}],
                'meshes': [], 'materials': [], 'accessors': [], 'bufferViews': []}

    def accessor(array, vector_type, target):
        while len(binary) % 4:
            binary.append(0)
        offset = len(binary)
        binary.extend(array.tobytes())
        view_index = len(document['bufferViews'])
        document['bufferViews'].append({'buffer': 0, 'byteOffset': offset, 'byteLength': array.nbytes, 'target': target})
        index = len(document['accessors'])
        entry = {'bufferView': view_index, 'componentType': 5126 if array.dtype.kind == 'f' else (5123 if array.dtype.itemsize == 2 else 5125),
                 'count': len(array), 'type': vector_type}
        if vector_type == 'VEC3':
            entry.update(min=array.min(axis=0).tolist(), max=array.max(axis=0).tolist())
        document['accessors'].append(entry)
        return index

    for part in parts:
        triangles = part['triangles']
        normals = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
        lengths = np.linalg.norm(normals, axis=1)
        valid = lengths > 1e-15
        triangles, normals, lengths = triangles[valid], normals[valid], lengths[valid]
        normals /= lengths[:, None]
        records = np.concatenate([triangles.reshape(-1, 3), np.repeat(normals, 3, axis=0)], axis=1).astype('<f4')
        vertices, indices = np.unique(records, axis=0, return_inverse=True)
        position = accessor(np.ascontiguousarray(vertices[:, :3]), 'VEC3', 34962)
        normal = accessor(np.ascontiguousarray(vertices[:, 3:]), 'VEC3', 34962)
        # Extra articulated links must not inflate transfer needlessly: lossless
        # 16-bit indexing when the vertex count permits it; no mesh decimation.
        index = accessor(indices.astype('<u2' if len(vertices) <= 65536 else '<u4'), 'SCALAR', 34963)
        material = len(document['materials'])
        surface = part.get('surface', 'silver')
        finish = SURFACES[surface]
        document['materials'].append({'name': part['name'], 'extras': {'surface': surface}, 'pbrMetallicRoughness': {
            'baseColorFactor': finish['color'], 'metallicFactor': finish['metalness'], 'roughnessFactor': finish['roughness']}})
        mesh = len(document['meshes'])
        document['meshes'].append({'name': part['name'], 'primitives': [{
            'attributes': {'POSITION': position, 'NORMAL': normal}, 'indices': index, 'material': material}]})
        document['nodes'][0]['children'].append(len(document['nodes']))
        document['nodes'].append({'name': part['name'], 'mesh': mesh, 'extras': {
            'original_mesh': part['source'], 'original_link': part.get('link', part['name'])}})
    document['buffers'] = [{'byteLength': len(binary)}]
    encoded = json.dumps(document, separators=(',', ':'), ensure_ascii=True).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    binary += b'\0' * (-len(binary) % 4)
    payload = (struct.pack('<III', 0x46546C67, 2, 28 + len(encoded) + len(binary)) +
               struct.pack('<II', len(encoded), 0x4E4F534A) + encoded +
               struct.pack('<II', len(binary), 0x004E4942) + binary)
    if path is not None:
        path.write_bytes(payload)
    return payload  # path=None: offline in-memory export/verification, no writes.


def build_assets():
    manifest = verify_sources()
    catalog, report = [], []
    for tool_id, name, package in TOOLS:
        if tool_id == 'robotiq-3f':
            assembly = f'{package}/cfg/robotiq-3f-gripper_articulated.urdf'
            urdf = ET.parse(SOURCE / assembly).getroot()
        else:
            stroke = 85 if tool_id == 'robotiq-2f85' else 140
            assembly = f'{package}/urdf/robotiq_arg2f_{stroke}_model_macro.xacro'
            urdf = expanded_2f(package, stroke)
        parts, animation = animation_source(tool_id)
        vertices = np.concatenate([part['triangles'].reshape(-1, 3) for part in parts])
        bounds_min, bounds_max = vertices.min(axis=0) * 1000, vertices.max(axis=0) * 1000
        if not np.isfinite(vertices).all() or bounds_max.max() > 1000 or bounds_min.min() < -500:
            raise ValueError(f'Invalid tool dimensions: {tool_id}')
        # Model-derived, static-open grasp reference, never a physical calibration.
        pads = [part['triangles'].reshape(-1, 3).mean(axis=0) for part in parts
                if part['name'].endswith('inner_finger_pad')]
        tcp = np.round(np.mean(pads, axis=0) * 1000, 6).tolist() if pads else [0, 0, 211.125]
        reference = ('Mean of the two URDF contact-pad centres at zero joint angle; not a calibrated controller TCP.'
                     if pads else 'Approximate shared-STL open grasp centre at CAD Z=50 mm, shifted with mount datum; not calibrated.')
        source = f'https://github.com/{REPO}/tree/{COMMIT}/{package}'
        entry = {'id': tool_id, 'name': name, 'type': 'parallel_gripper', 'asset': f'/tools/{tool_id}.glb',
                 'source': source, 'license': 'BSD-2-Clause', 'tcp_mm': tcp,
                 'bounds_mm': {'min': np.round(bounds_min, 6).tolist(), 'max': np.round(bounds_max, 6).tolist()},
                 'default_rotation_deg': [0, 0, 0], 'units': 'm', 'commit': COMMIT,
                 'source_manifest': '/tools/source/robotiq/manifest.json',
                 'license_file': '/tools/source/robotiq/LICENSE',
                 'tcp_reference': reference, 'grip_fingers': 3 if tool_id == 'robotiq-3f' else 2,
                 'pose': 'fixed-open', 'calibrated': False, 'animation': animation,
                 'assembly_source': assembly, 'preview': f'/tools/{tool_id}.png'}
        metadata = {'source': source, 'commit': COMMIT, 'license': entry['license'], 'units': 'm',
                    'tcp_reference': entry['tcp_reference'], 'original_units': '2F-85 DAE millimetres scaled 0.001 by URDF; STL metres',
                    'modifications': 'Source-linked rigid-joint assembly baked at open pose; normals recomputed; articulated 3F +Y mapped to flange +Z and rear palm normalized; neutral materials.',
                    'pose': 'open bind pose; simulated rigid joints, no physics or IO', 'source_files': len(manifest['files']),
                    'animation': animation}
        output = OUT / f'{tool_id}.glb'
        write_glb(output, parts, metadata)
        entry['sha256'] = digest(output.read_bytes())
        catalog.append(entry)
        report.append({'id': tool_id, 'bytes': output.stat().st_size, 'parts': len(parts),
                       'triangles': sum(len(part['triangles']) for part in parts), 'sha256': entry['sha256'],
                       'bounds_mm': entry['bounds_mm'], 'tcp_mm': tcp,
                       'mesh_sources': sorted({part['source'] for part in parts if part['source']}),
                       'source_coordinate_notes': metadata['modifications']})
        render_preview(OUT / f'{tool_id}.png', parts)
    extra_catalog, extra_report = build_extra_assets()
    catalog.extend(extra_catalog); report.extend(extra_report)
    write_json(OUT / 'catalog.json', {'schema_version': 1, 'tools': catalog})
    write_json(OUT / 'asset-report.json', report)
    print(json.dumps(report, ensure_ascii=False))


def render_preview(path, parts):
    """Offline orthographic CPU raster, not a browser/device or a fabricated CAD."""
    import zlib
    width, height = 480, 420
    image = np.full((height, width, 3), [12, 21, 27], dtype=np.uint8)
    depth = np.full((height, width), -np.inf)
    all_vertices = np.concatenate([part['triangles'].reshape(-1, 3) for part in parts])
    # Look from +X,-Y,+Z; keep model +Z vertical on screen.
    right = np.array([.8660254, .5, 0])
    up = np.array([-.17101, .296198, .939693])
    forward = np.cross(right, up)
    camera = np.array([right, up, forward])
    projected = all_vertices @ camera.T
    minimum, maximum = projected.min(axis=0), projected.max(axis=0)
    scale = min((width - 60) / (maximum[0] - minimum[0]), (height - 50) / (maximum[1] - minimum[1]))
    centre = (minimum + maximum) / 2
    for part in parts:
        points = part['triangles'] @ camera.T
        points[:, :, 0] = (points[:, :, 0] - centre[0]) * scale + width / 2
        points[:, :, 1] = -(points[:, :, 1] - centre[1]) * scale + height / 2
        normal = np.cross(part['triangles'][:, 1] - part['triangles'][:, 0], part['triangles'][:, 2] - part['triangles'][:, 0])
        normal /= np.maximum(np.linalg.norm(normal, axis=1, keepdims=True), 1e-15)
        shade = .35 + .65 * np.abs(normal @ np.array([.4, -.5, .7681146]))
        base = np.sqrt(np.array(part['color'][:3])) * 255
        for triangle, light in zip(points, shade):
            x0, y0 = np.maximum(np.floor(triangle[:, :2].min(axis=0)).astype(int), [0, 0])
            x1, y1 = np.minimum(np.ceil(triangle[:, :2].max(axis=0)).astype(int), [width - 1, height - 1])
            if x1 < x0 or y1 < y0:
                continue
            a, b, c = triangle
            denominator = (b[1]-c[1])*(a[0]-c[0])+(c[0]-b[0])*(a[1]-c[1])
            if abs(denominator) < 1e-12:
                continue
            yy, xx = np.mgrid[y0:y1+1, x0:x1+1]
            u = ((b[1]-c[1])*(xx-c[0])+(c[0]-b[0])*(yy-c[1])) / denominator
            v = ((c[1]-a[1])*(xx-c[0])+(a[0]-c[0])*(yy-c[1])) / denominator
            w = 1-u-v
            z = u*a[2]+v*b[2]+w*c[2]
            hit = (u >= 0) & (v >= 0) & (w >= 0) & (z > depth[y0:y1+1, x0:x1+1])
            depth[y0:y1+1, x0:x1+1][hit] = z[hit]
            image[y0:y1+1, x0:x1+1][hit] = np.minimum(base * light, 255).astype(np.uint8)
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data))
    raw = b''.join(b'\0' + row.tobytes() for row in image)
    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--download', action='store_true', help='download pinned public source files first')
    parser.add_argument('--download-extra', action='store_true', help='download pinned additional manufacturers')
    args = parser.parse_args()
    if args.download:
        download_sources()
        download_extra_sources()
    elif args.download_extra:
        download_extra_sources()
    build_assets()
