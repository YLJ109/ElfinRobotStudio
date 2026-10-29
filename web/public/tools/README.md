# Local end-tool library

Six distinct real open-source end tools are assembled from pinned upstream
visual meshes. No network connection is required at runtime. These are display
and simulation references, not a calibration or a certified safety model.

| ID | Source assembly | Static pose | Reference TCP / mm |
| --- | --- | --- | --- |
| `robotiq-2f85` | official ROS-Industrial 2F-85 xacro, 11 visual parts | joints = 0, open | `(0, 0, 130.324)` |
| `robotiq-2f140` | official ROS-Industrial 2F-140 xacro, 11 visual parts | joints = 0, open | `(0, 0, 176.963104)` |
| `robotiq-3f` | upstream complete fixed-open URDF, palm + all three fingers | fixed open | `(0, 0, 211.125)`, approximate |
| `franka-panda-hand` | Franka official hand + both fingers, nine material groups | 40 mm per finger | `(0, 0, 103.4)`, upstream TCP frame |
| `schunk-sdh` | Schunk SDH full palm and three articulated fingers, ten parts | joints = 0 | `(0, 0, 150)`, upstream grasp frame |
| `schunk-wsg50` | WSG50 complete body + stock fingers with tips, three parts | extra opening ±30 mm | `(0, 0, 144.382999)`, approximate contact centre |

## 2026-10-26 expanded sources and finish

The three added models are different mechanisms/manufacturers, not resized copies.

| Source | Pinned commit | License / preserved notice |
| --- | --- | --- |
| [Franka Robotics](https://github.com/frankarobotics/franka_ros) | `ddd2fffd9de44b02ad15b4bbb2bfa2cec4d60d98` | [Apache-2.0](source/franka/LICENSE), [NOTICE](source/franka/NOTICE) |
| [IPA Schunk](https://github.com/ipa320/schunk_modular_robotics) | `b2d73bdd333eceda0e91b4f8d4e8881433dc243a` | [Apache-2.0](source/schunk/LICENSE) |
| [RobotLocomotion WSG50](https://github.com/RobotLocomotion/models/tree/master/wsg_50_description) | `f85076b4d95a8bdfec70153015e723e92312e4e2` | [BSD-3-Clause](source/wsg/wsg_50_description/LICENSE) |

Every added source directory contains its own `manifest.json` with the original
URLs, commit, byte counts and SHA-256 hashes. The WSG50 upstream README explicitly
states these meshes were independently constructed from published dimensioned
drawings, not Schunk/CADENAS CAD. This limitation is retained; they are real
licensed tool geometry, not manufacturer-supplied CAD or a certified calibration.

All six GLBs together are under **7 MiB** and only the selected GLB is fetched.
Preview images also load only for the selected dropdown entry; no model prefetch,
remote decoder or remote textures are added. Raw originals remain local for audit.

The updated GLBs use neutral silver PBR metal (metalness .65, roughness .28),
black metal (.5/.34) and dark rubber (.02/.8). Original rubber/contact material
groups are preserved where supplied. No glow or fabricated animation is used.
These are display finish changes, not claims about original manufacturer's colors.
`applyToolSkin(root, 'factory'|'cyber')` in `toolVisual.js` permits the scene owner
to synchronize the tool finish with the existing robot skin and RoomEnvironment.
`readLibraryTool` applies factory finish on load; scene-wide switching remains
the scene owner's integration. In cyber mode **only silver metal** matches the
robot's `#8d9aaa`, metalness .6 and roughness .3. Black structural metal stays
black, gripper rubber stays black, and white suction cups stay white in both
skins. Environment intensity remains 1. Factory restores the silver reference
finish without changing those semantic colors. Empty mounts are a no-op.

Franka's authoritative xacro TCP is 103.4 mm; Schunk's grasp joint is 150 mm.
WSG50 needs both the glTF Y-up → SDFormat Z-up loader convention and the source
+Y extension → library +Z mount transform. Its rear body face is normalized to
zero; the TCP is the midpoint of the SDF contact references. These transforms
are baked into the self-contained GLB, while bounds are measured from final
geometry. Small negative flange protrusions in other tools are preserved.

Joint descriptions are retained for future actuator support, but no fake opening
animation, payload identification or physical IO was added. The static poses
must not be presented as a real mechanism simulation.

The 3F mesh is the upstream low-detail visual assembly, not a block proxy. Its
upstream schema category is represented by the library's `parallel_gripper` enum;
it is in fact a three-finger adaptive gripper, not a two-finger parallel mechanism.
`grip_fingers: 3` records this distinction. No opening/closing animation, actuator
driver, live payload or IO is implied by selecting any of these tools.

## Sources and licensing

- Repository: https://github.com/ros-industrial-attic/robotiq
- Pinned commit: `45196f6558fe8ba9d89bc8a105396c68c3e7e892` (`kinetic-devel`).
- All three visualization packages declare `BSD` in their retained `package.xml`.
- The retained root [LICENSE](source/robotiq/LICENSE) is BSD-2-Clause, copyright
  2013 ROS-Industrial. Original embedded author/copyright notices remain intact.
- [Original manifest](source/robotiq/manifest.json) records the exact URL, SHA-256,
  length and commit for every byte-identical downloaded file.
- The current official https://github.com/robotiq/robotiq-ros repository was also
  inspected at `be8aa38abb2f84e85adb9d6862092b9dfc0124c5`; its BSD-3-Clause license
  is not substituted for the license of this separately pinned source set.

Original mesh and xacro/URDF files are retained under `source/robotiq/`. Generated
GLBs include attribution and transformation notes in `asset.extras`. Raw source
contains its original notices and metadata. No external texture/decoder is used.

## Coordinate convention and TCP

GLB positions are **metres**, with the installation flange at the origin and
tool extension along **+Z**. Catalog bounds and TCP are **millimetres**, expressed
in that same unrotated installation frame. Rotation is applied by the scene's
common tool mount, not baked into the catalog values.

- 2F-85 DAE numeric positions are in millimetres; xacro explicitly supplies a
  `0.001` mesh scale. Original mesh coordinates are preserved; the inconsistent
  `Y_UP` metadata on one export is not interpreted as an extra URDF rotation.
- 2F-140 STL positions and URDF joint/visual origins are in metres.
- 2F flange origin follows the upstream base-link datum. Small body protrusions
  below Z=0 are retained in the bounds, not silently cropped or recentered.
- 3F uses the original shared CAD datum. The palm installation plane is at
  CAD Z=-161.125 mm; the assembled mesh is translated by +161.125 mm to put it
  at local Z=0. All original relative palm/finger positions are unchanged.
- 2F TCP is calculated from the mean of both **contact-pad centres** in the
  zero-angle URDF assembly. 3F uses an approximate central grasp reference at
  CAD Z=+50 mm, shifted by the same mounting-datum correction. Neither is a
  manufacturer-certified tool calibration; 3F precision is particularly limited.

Only normals, indexing, static assembly, 3F datum normalization and 3F diagnostic
green-to-neutral materials are changed. The model-library selection must never
write TCP, load, actuator commands or IO to a real controller.

## Reproducible build and integration

From the project root:

```powershell
.venv/Scripts/python.exe tools/build_tool_assets.py
# To deliberately fetch the pinned originals again first:
.venv/Scripts/python.exe tools/build_tool_assets.py --download
node --test sim/web/scripts/check-tool-library.mjs
```

Build uses Python + the project's NumPy, validates every original hash and
produces self-contained GLBs, CPU-rendered preview PNGs, `catalog.json` and
`asset-report.json`. Preview PNGs are real source-mesh renders, not concept art.
Build does not use a camera, browser, running backend, controller or real robot.

Frontend contract:

- `ModelLibrary.selectedToolId`: catalog ID, `vacuum-reference`, or `none`.
  `selectedToolRotation` is an optional authoritative three-angle array.
- Draft selection is separate from the active-tool marker. Only confirmation
  by the parent updates the marker. One switch button emits
  `{kind:'library', id, rotation}`; built-in vacuum emits its existing reference
  descriptor, and no-tool emits `null`.
- `readLibraryTool({kind:'library', id})` finds the canonical entry itself,
  rejects non-allowlisted URLs, checks GLB integrity and measured bounds, and
  returns a fresh Three.js root with `userData.tcp_mm` and `bounds_mm`.
- `root.userData` comes from the local catalog, never caller-supplied TCP/bounds.
  The caller owns and must dispose the returned scene with `disposeAsset`.
- The scene places the root at its common flange mount and the TCP axis at
  `root.userData.tcp_mm / 1000`. Simulation/backend catalog switching is a
  separate parent-owned integration; this component does not call device APIs.
