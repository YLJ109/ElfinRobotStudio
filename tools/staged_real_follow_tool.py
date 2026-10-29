"""Staged, pure real-tool binding; never imports backend, stores or transport.

Integration contract (mainline only): resolve one ToolStore mounted/profile
snapshot; expose all blockers; put context_fingerprint into both follow capture
and planning context. Only build a model when ok. Pass RobotModel explicitly and
TCP_OFFSET_Z_MM explicitly; do not use sim_tool. No integration is installed here.

Non-none profiles require mount/TCP/payload/geometry verification records with
record_id, method, verified_by, verified_at (caller-issued provenance strings),
tool_revision, mounted_revision and subject_sha256. evidence_digest binds records
to the exact section and revisions. This checks consistency, not authenticity:
the caller must obtain records from a trusted validation workflow, not user
photos or arbitrary client assertions. Geometry is an explicitly verified
conservative capsule envelope, in mount coordinates, mm. TCP is flange-relative
(including the mount), mm/deg. Payload COM is flange-relative, mm.
"""
from copy import copy, deepcopy
import hashlib
import json
import math


def _json_value(value, depth=0, budget=None):
    budget = [20000] if budget is None else budget
    budget[0] -= 1
    if depth > 20 or budget[0] < 0:
        raise ValueError('Input exceeds structural budget')
    kind = type(value)
    if value is None or kind is bool:
        return
    if kind in (int, float):
        if not math.isfinite(value):
            raise ValueError('Non-finite number')
        return
    if kind is str:
        if len(value) > 8192:
            raise ValueError('String exceeds budget')
        return
    if kind is dict:
        for key, item in value.items():
            if type(key) is not str:
                raise ValueError('Object keys must be strings')
            _json_value(key, depth + 1, budget)
            _json_value(item, depth + 1, budget)
        return
    if kind is list:
        for item in value:
            _json_value(item, depth + 1, budget)
        return
    raise ValueError('Only plain JSON values accepted')


def _digest(value):
    _json_value(value)
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True, allow_nan=False).encode()).hexdigest()


def evidence_digest(section, tool_revision, mounted_revision):
    """Digest for a trusted record producer; never creates verification records."""
    return _digest({'value': {k: v for k, v in section.items() if k != 'verification'},
                    'tool_revision': tool_revision, 'mounted_revision': mounted_revision})


def _vector(value, size):
    return (type(value) is list and len(value) == size
            and all(type(v) in (int, float) and math.isfinite(v) and abs(v) <= 1e6 for v in value))


def resolve_real_follow_tool(mounted, tool, *, config_revision, variant):
    """Return {ok, blockers, context_fingerprint, binding}; never fallback to none.

    Fingerprint includes the complete mount/profile/config revision/variant,
    even on blocked snapshots, so unversioned edits also invalidate a capture.
    """
    blockers = []

    def block(layer, code, path, detail):
        blockers.append(dict(layer=layer, code=code, path=path, detail=detail))

    try:
        fingerprint = _digest({'mounted': mounted, 'tool': tool,
                               'config_revision': config_revision, 'variant': variant})
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        block('input', 'invalid_json', '$', str(exc))
        return dict(ok=False, blockers=blockers, context_fingerprint=None, binding=None)
    mounted = deepcopy(mounted) if type(mounted) is dict else {}
    tool = deepcopy(tool) if type(tool) is dict else {}
    if not (type(config_revision) is int and config_revision >= 0 and type(variant) is str and variant.strip()):
        block('context', 'invalid_context', 'config_revision/variant', 'Explicit configuration revision and robot variant required')
    tool_id = mounted.get('tool_id')
    none = tool_id == 'none' and tool.get('id') == 'none' and tool.get('type') == 'none'
    allowed = ('none_confirmed', 'confirmed', 'manual_confirmed', 'detected_verified') if none else ('manual_confirmed', 'detected_verified')
    if mounted.get('state') not in allowed:
        block('identity', 'not_confirmed', 'mounted.state', 'Actual installed tool must be explicitly confirmed')
    expected_source = 'detected' if mounted.get('state') == 'detected_verified' else 'manual'
    if mounted.get('source') != expected_source:
        block('identity', 'invalid_source', 'mounted.source', 'Confirmation source must match installation state')
    if mounted.get('scope', 'real') != 'real':
        block('identity', 'simulation_scope', 'mounted.scope', 'Simulation descriptors cannot bind real hardware')
    if not (type(tool_id) is str and tool_id.strip() and tool_id == tool.get('id')):
        block('identity', 'profile_mismatch', 'tool.id', 'Mounted identity must match an existing profile')
    if not none and tool.get('type') not in ('suction', 'parallel_gripper', 'custom'):
        block('identity', 'unsupported_type', 'tool.type', 'Explicit physical tool type required')
    rev = mounted.get('revision')
    if type(rev) is not int or rev < 1:
        block('identity', 'invalid_revision', 'mounted.revision', 'Positive installation revision required')
    if type(tool.get('revision')) is not str or not tool['revision'].strip():
        block('identity', 'invalid_revision', 'tool.revision', 'Profile revision required')
    identity = tool.get('identity') if type(tool.get('identity')) is dict else {}
    if mounted.get('hardware_id') is not None and mounted['hardware_id'] != identity.get('hardware_id'):
        block('identity', 'hardware_mismatch', 'mounted.hardware_id', 'Detected hardware differs from profile')

    sections = {}
    for name in ('mount', 'tcp', 'payload', 'geometry'):
        section = tool.get(name)
        section = section if type(section) is dict else {}
        sections[name] = section
        if none and name in ('mount', 'geometry'):
            continue
        if section.get('status') != 'verified':
            block(name, 'not_verified', 'tool.' + name + '.status', 'Verified physical data required')
        if none:
            continue  # Built-in zero tool is validated numerically below.
        record = section.get('verification')
        record = record if type(record) is dict else {}
        if (not all(type(record.get(k)) is str and record[k].strip()
                    for k in ('record_id', 'verified_by', 'verified_at'))
                or record.get('method') not in ('measured', 'calibrated', 'manufacturer', 'inspection')):
            block(name, 'missing_record', 'tool.' + name + '.verification', 'Trusted validation record required; photo dimensions alone are insufficient')
        if (record.get('tool_revision') != tool.get('revision')
                or type(record.get('mounted_revision')) is not int or record.get('mounted_revision') != rev
                or record.get('subject_sha256') != evidence_digest(section, tool.get('revision'), rev)):
            block(name, 'stale_record', 'tool.' + name + '.verification', 'Record must bind these exact values and installation/profile revisions')
    mount, tcp, payload, geometry = (sections[k] for k in ('mount', 'tcp', 'payload', 'geometry'))
    if not _vector(mount.get('translation_mm'), 3) or not _vector(mount.get('quaternion_xyzw'), 4):
        block('mount', 'invalid_transform', 'tool.mount', 'Mount requires finite translation and unit quaternion')
    elif not math.isclose(sum(v*v for v in mount['quaternion_xyzw']), 1., abs_tol=1e-8):
        block('mount', 'invalid_quaternion', 'tool.mount.quaternion_xyzw', 'Quaternion must be normalized')
    if not _vector(tcp.get('translation_mm'), 3) or not _vector(tcp.get('rpy_deg'), 3):
        block('tcp', 'invalid_tcp', 'tool.tcp', 'Complete finite TCP translation and rotation required')
    if not none and tcp.get('frame') != 'flange':
        block('tcp', 'invalid_frame', 'tool.tcp.frame', 'TCP must include mount and be flange-relative')
    mass = payload.get('mass_kg')
    if type(mass) not in (int, float) or not math.isfinite(mass) or mass < 0 or (not none and mass <= 0) or not _vector(payload.get('com_mm'), 3):
        block('payload', 'invalid_payload', 'tool.payload', 'Measured positive mass and finite COM required for a physical tool')
    if not none and payload.get('frame') != 'flange':
        block('payload', 'invalid_frame', 'tool.payload.frame', 'COM must be flange-relative')
    if 'inertia_kgm2' in payload:
        inertia = payload['inertia_kgm2']
        valid = _vector(inertia, 6)
        if valid:
            x, y, z, xy, xz, yz = inertia
            valid = (min(x, y, z) >= 0 and x+y >= z and x+z >= y and y+z >= x
                     and x*y >= xy*xy and x*z >= xz*xz and y*z >= yz*yz
                     and x*y*z+2*xy*xz*yz-x*yz*yz-y*xz*xz-z*xy*xy >= 0)
        if not valid:
            block('payload', 'invalid_inertia', 'tool.payload.inertia_kgm2', 'Supplied inertia must be physically valid; never synthesized')
    capsules = geometry.get('capsules')
    if not none:
        if geometry.get('frame') != 'mount' or geometry.get('conservative') is not True:
            block('geometry', 'invalid_envelope', 'tool.geometry', 'Verified conservative capsule envelope in mount coordinates required')
        if type(capsules) is not list or not 1 <= len(capsules) <= 128:
            block('geometry', 'missing_capsules', 'tool.geometry.capsules', 'Explicit nonempty physical envelope required')
        else:
            for i, cap in enumerate(capsules):
                if (type(cap) is not dict or not _vector(cap.get('a'), 3) or not _vector(cap.get('b'), 3)
                        or type(cap.get('r_mm')) not in (int, float) or not 0 < cap['r_mm'] <= 1e6):
                    block('geometry', 'invalid_capsule', f'tool.geometry.capsules[{i}]', 'Finite endpoints and positive radius required')
    elif (mount.get('translation_mm') != [0, 0, 0] or mount.get('quaternion_xyzw') != [0, 0, 0, 1]
          or tcp.get('translation_mm') != [0, 0, 0] or tcp.get('rpy_deg') != [0, 0, 0]
          or mass != 0 or payload.get('com_mm') != [0, 0, 0] or geometry):
        block('identity', 'nonzero_none', 'tool', 'None must remain the built-in zero tool without a body')
    return dict(ok=not blockers, blockers=blockers, context_fingerprint=fingerprint,
                binding=None if blockers else dict(mounted=mounted, tool=tool, variant=variant, config_revision=config_revision))


def build_real_follow_model(mounted, tool, *, config_revision, variant, model_factory, flange_offset_z_mm):
    """Explicit pure factory. Blocked result has model=None; never flange fallback.

    The injected factory must be a pure kinematics constructor. The returned
    adapter forwards IK/FK to that SAME TCP-bound model; capsules and plane gaps
    cover the validated physical body. It does not send controller TCP/payload.
    """
    result = resolve_real_follow_tool(mounted, tool, config_revision=config_revision, variant=variant)
    result['model'] = None
    if not result['ok']:
        return result
    if type(flange_offset_z_mm) not in (int, float) or not math.isfinite(flange_offset_z_mm):
        result.update(ok=False, binding=None)
        result['blockers'].append(dict(layer='model', code='invalid_flange_offset', path='flange_offset_z_mm', detail='Explicit finite kinematics flange offset required'))
        return result
    bound = result['binding']['tool']
    translation = list(bound['tcp']['translation_mm'])
    # TCP is relative to the physical flange. RobotModel already adds the
    # URDF-to-physical-flange offset; pass TCP unchanged (including for none).
    try:
        base = model_factory(variant, tcp_translation_mm=translation, tcp_rpy_deg=list(bound['tcp']['rpy_deg']))
        result['model'] = _PhysicalToolModel(base, bound, flange_offset_z_mm)
    except Exception as exc:
        result.update(ok=False, binding=None)
        result['blockers'].append(dict(layer='model', code='construction_failed', path='model_factory', detail=f'{type(exc).__name__}: {exc}'))
    return result


class _PhysicalToolModel:
    def __init__(self, base, profile, offset):
        object.__setattr__(self, '_base', base)
        object.__setattr__(self, '_profile', deepcopy(profile))
        object.__setattr__(self, '_offset', offset)

    def __copy__(self):
        base = copy(self._base)
        base._fk_key, base._fk_value = None, None
        return type(self)(base, self._profile, self._offset)

    def __setattr__(self, name, value):
        # World clears FK caches on worker copies. Clear the actual IK model.
        setattr(self._base, name, value)

    def __getattr__(self, name):
        return getattr(self._base, name)

    def _tool_capsules(self, q):
        if self._profile['id'] == 'none':
            return []
        matrix = self._base.fk(q)[self._base.flange]
        mount = self._profile['mount']
        x, y, z, w = mount['quaternion_xyzw']
        rotation = [[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                    [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                    [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]]

        def point(p):
            local = [mount['translation_mm'][i]+sum(rotation[i][j]*p[j] for j in range(3)) for i in range(3)]
            local[2] += self._offset
            return [matrix[i][3]*1000+sum(matrix[i][j]*local[j] for j in range(3)) for i in range(3)]

        return [dict(link=self._base.flange, a=point(c['a']), b=point(c['b']), r_mm=c['r_mm'])
                for c in self._profile['geometry']['capsules']]

    def capsules(self, q, shrink=0.90, conservative=False):
        return self._base.capsules(q, shrink=shrink, conservative=conservative) + self._tool_capsules(q)

    def tool_plane_gaps(self, q, fence):
        if self._profile['id'] == 'none':
            return self._base.tool_plane_gaps(q, fence)
        caps = self._tool_capsules(q)
        planes = [('fence', '正面墙', 0, 1, -fence['x'][0]),
                  ('fence', '背面墙', 0, -1, fence['x'][1]),
                  ('fence', '右侧墙', 1, 1, -fence['y'][0]),
                  ('fence', '左侧墙', 1, -1, fence['y'][1]), ('ground', '地面', 2, 1, 0)]
        return [dict(type=kind, surface=name, link=self._base.flange, geometry='verified-capsule',
                     gap_mm=min(min(sign*c['a'][axis], sign*c['b'][axis])+offset-c['r_mm'] for c in caps))
                for kind, name, axis, sign, offset in planes]
