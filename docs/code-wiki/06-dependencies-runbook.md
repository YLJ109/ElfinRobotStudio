# 06 · 依赖与运行手册

## 1. 版本矩阵

### 1.1 运行时

| 组件 | 要求/版本 | 依据 |
| --- | --- | --- |
| Python | 3.11+ | [pyproject.toml](../../pyproject.toml) |
| Node.js / npm | 以 Vite 6 与 Electron 构建实际要求为准；按锁文件安装 | [web/package-lock.json](../../web/package-lock.json)、[package-lock.json](../../package-lock.json) |
| FastAPI | 0.143.2 | [backend/requirements.txt](../../backend/requirements.txt) |
| Pydantic | 2.14.1 | 同上 |
| Uvicorn | 0.54.0（standard） | 同上 |
| httpx | 0.28.1 | 同上 |
| websockets | 17.2 | 同上 |
| numpy | 2.5.4 | 同上 |
| Vue | 3.5.13 | [web/package.json](../../web/package.json) |
| Pinia | 2.2.6 | 同上 |
| Three.js | 0.170.0 | 同上 |
| Vite | 6.4.4 | 同上 |
| CodeMirror | 6.x | 同上 |
| meshoptimizer | 1.3.0 | 同上 |
| Electron | 45.1.0 | [package.json](../../package.json) |
| electron-builder | 26.18.0 | 同上 |

### 1.2 可选环境

| 环境 | 用途 | 位置 |
| --- | --- | --- |
| `.venv-vision` | MediaPipe 手掌检测（源码/打包） | `backend/.venv-vision` |
| `requirements-vision*.txt` | 视觉依赖锁/构建 | `backend/` |
| RealSense SDK / OpenCV | 真实相机 | `pyrealsense2`、`cv2`（运行时可选） |
| Tabbit/Chromium | 浏览器/GPU 验收 | 外部工具，不是项目依赖 |

缺失相机 SDK 只影响相机页，不影响项目启动（[env_doctor.ps1](../../tools/env_doctor.ps1#L33-L38)）。

## 2. 依赖关系

### 2.1 后端

```text
backend/main.py
  ├─ bootstrap.ApplicationResources
  ├─ core: config, world, program_store, cameras, scenes, vision_workspace
  ├─ application: vision_pipeline, remote_camera, drawing, sorting_score
  ├─ infrastructure.vision: process_detector
  ├─ routes: task/drawing/teaching/vision/workspace/data_backup/run_journal/
  │         scene_asset/handling/assembly/vision_tasks/play/robot_play/
  │         motion_session/debug
  └─ schemas, access, request_limits, build_identity

core/world.py（中心）
  ├─ config, controls, ai_plan, program, telemetry
  ├─ eventlog, motion, storage, calibration
  ├─ planning, kinematics, safety
  ├─ objects, points, tools, sim_tool, tool_library
  ├─ robot_sim, template/trajectory, hand_follow, hand_track
  ├─ real_follow_link, real_jog_follow, real_follow_runtime,
  │   real_movel_qualification, real_follow_tool_binding
  ├─ domain: suction, motion_target
  └─ application.run_journal（唯一 core→application 反向引用）

application/*
  ├─ drawing → planning/trajectory/kinematics/safety
  ├─ robot_play → program/planning/safety/play_routes/play_vision
  ├─ assembly → objects/play_vision/play_routes
  ├─ handling → domain.handling_challenge/objects/planning/play_routes
  ├─ vision_pipeline → 仅依赖注入的 detector 与 CameraManager
  ├─ remote_camera → remote_clock
  ├─ data_backup → objects/scenes/scene_assets/tools/workspace_routes
  └─ run_journal → Runner 事件（只观察）
```

依赖规则：

- `core/` 不依赖 `routes/`；`application/` 不直接建 FastAPI app；
- 路由只依赖注入的 `World`/服务，不打开设备；
- `infrastructure/` 只依赖 `paths` 与标准库，不反向依赖 `core`；
- 新增跨层依赖时优先通过参数注入，避免循环 import。

### 2.2 前端

```text
main.js
  ├─ App.vue
  │   ├─ store.js（Pinia）
  │   ├─ scene.js（Three.js）
  │   ├─ panels/*
  │   └─ components/*
  ├─ desktopStartup.js
  ├─ tabNavigation.js
  └─ panelFolding.js

store.js
  ├─ connectionState.js
  ├─ targetState.js
  ├─ runtimeMetrics.js
  ├─ aiConversation.js
  └─ sceneObjectEditing.js

scene.js
  ├─ toolVisual.js、visualAssets.js
  ├─ jointCaps.js、robotSurface*.js
  ├─ objectCatalog.js、objectGround.js
  ├─ virtualCamera.js、drawingVisual.js
  ├─ sceneAssetVisual.js、robotSurfaceAsset.js

任务控制器
  ├─ drawingTask.js → drawingPreview.js/textContours.js/writingConfig.js
  ├─ chessMatch.js → robotPlayProgram.js/functionPlay.js
  ├─ practicalTasks.js → functionPlay.js/robotPlayProgram.js
  ├─ handFollowInput.js → handPipeline.js/cameraSource.js
  └─ visionWorkspace.js → virtualCamera.js/visionLabels
```

约束：纯逻辑模块不 import 面板；面板不直接 new WebSocket；`scene.js` 是唯一 Three.js 资源所有者。

## 3. 环境准备

### 3.1 只检查，不修复

```powershell
powershell -NoProfile -File tools/env_doctor.ps1 -CheckOnly
node --version
npm.cmd --version
```

`-CheckOnly` 只检查，不重建 venv/不装依赖。

### 3.2 修复环境

```powershell
powershell -NoProfile -File tools/env_doctor.ps1
```

[env_doctor.ps1](../../tools/env_doctor.ps1)：

- 检查 `backend/.venv` 的 `pyvenv.cfg` 是否指向本机 Python；
- 优先 `python -m venv --upgrade` 修转发器，保留 `site-packages`；
- 只有 `-Rebuild` 才重建，旧 venv 先改名备份；
- 缺失依赖时按 `requirements.txt` 安装；
- 退出码：0 可用 / 2 修不好 / 3 无 Python 3.11+。

### 3.3 前端依赖

```powershell
npm.cmd install
npm.cmd --prefix web install
```

按锁文件安装；不要随意升级 Electron/Vite/Three 主版本。

## 4. 启动方式

### 4.1 开发网页（推荐）

```powershell
$env:HUYAN_NO_REAL = '1'
.\start.bat dev
# http://localhost:5173/
```

实际行为：

1. `run_dev.ps1` 调 `run_backend.ps1 -Ensure -Reload`；
2. 后端 8710 启动并健康检查；
3. Vite 5173 复用或启动；
4. `start.bat` 打开浏览器。

`start.bat` / `start` / `dev` / `restart` 都复用同一 5173/8710；`web` 已下线。

### 4.2 直接启动后端

```powershell
backend/.venv/Scripts/python.exe -m uvicorn backend.main:app --host 127.0.0.1 --port 8710
# 加 --reload 仅开发；不要在真机作业期间热重载
```

### 4.3 直接启动 Vite

```powershell
npm.cmd --prefix web run dev
```

Vite 代理 `/api`、`/ws` 到 8710。

### 4.4 桌面源码态

```powershell
npm.cmd start
# 或指定隔离数据：
$env:HUYAN_NO_REAL='1'
$env:ELFIN_DATA_DIR='G:\tmp\elfin-data'
npm.cmd start
```

桌面会优先复用兼容的 8710；若 8710 是纯 API 后端，会提示先 `start.bat stop`。

### 4.5 桌面打包态

```powershell
$env:HUYAN_NO_REAL='1'
& 'G:/MyProjects/ElfinRobotStudio/output/desktop-release/win-unpacked/JiuShuRobotStudio.exe'
```

### 4.6 停止

```powershell
.\start.bat stop
```

只停止可核验归属的 5173 Vite 与 8710 本仓库后端；不是物理急停。真机停止仍走界面/控制器停止事务。

## 5. 测试与检查

### 5.1 前端检查

```powershell
npm.cmd --prefix web test
```

- 运行 `web/scripts/check-*.mjs`（97 个）；
- 覆盖 store/导航/WS/目标状态/相机/跟随/绘制/棋局/场景/UI/构建入口；
- 输出失败会保留，不以总数掩盖单项。

### 5.2 桌面检查

```powershell
npm.cmd test
```

- 运行 `desktop/test/*.test.cjs`（14 个）；
- 覆盖后端会话、身份、权限、缓存、数据选择、日志、冒烟隔离、任务导航。

### 5.3 后端离线检查

```powershell
backend/.venv/Scripts/python.exe -B tools/run_offline_checks.py check_ai_intent.py
# 全量
backend/.venv/Scripts/python.exe -B tools/run_offline_checks.py
```

[run_offline_checks.py](../../tools/run_offline_checks.py)：

- 把 `backend/`、`contracts/`、`web/public/models|tools|vision`、`tools/` 复制到临时目录；
- 安装 `sitecustomize.py` 离线屏障；
- 强制 `ELFIN_DATA_DIR` 指向临时副本，禁止真实相机/外网；
- 每次检查清空临时数据，避免跨测试污染；
- 报告写 `output/checks/<temp-name>.json`，逐项日志同目录；
- 正式清单 [offline_checks.json](../../tools/offline_checks.json)：127 项检查 + 3 项诊断；
- 长检查 600 秒，其余 180 秒；超时会杀掉临时子进程。

**不要**直接导入原工作站 `World`，也不要用 `HUYAN_NO_REAL` 当相机/外网总开关。

### 5.4 文档检查

```powershell
node tools/docs.mjs check
node tools/docs.mjs find "吸盘 IO"
```

- `check`：索引重复、文件存在、关键词、本地链接、归档哈希、project.json 翻新任务依赖；
- `find`：本地字词检索，不是语义 RAG。

### 5.5 性能/诊断脚本

| 脚本 | 用途 |
| --- | --- |
| [check_start_latency.py](../../tools/check_start_latency.py) | 点击到首动/预检耗时 |
| [check_play_preflight_profile.py](../../tools/check_play_preflight_profile.py) | 取放预检热点（诊断项） |
| [prof_preflight.py](../../tools/prof_preflight.py) / [prof_sorting.py](../../tools/prof_sorting.py) | 预检/分拣剖析 |
| [qa_cdp.py](../../tools/qa_cdp.py) | 浏览器逐页截图 |
| [qa_first_move.py](../../tools/qa_first_move.py) | UI 层首动延迟 |
| [check_drawing.py](../../tools/check_drawing.py) / [check_text_writing.py](../../tools/check_text_writing.py) | 绘制/写字 |

诊断脚本不缩减正式清单；结果仍要绑定源码版本。

## 6. 构建

### 6.1 前端生产构建

```powershell
npm.cmd --prefix web run build
```

产物 `web/dist/`，并写 `build-identity.json`。构建期间源码变化会失败。

### 6.2 桌面打包

```powershell
backend/.venv/Scripts/python.exe tools/build_desktop.py
# 安装包（需独立发布授权）
backend/.venv/Scripts/python.exe tools/build_desktop.py --installer
```

产物见 [04 桌面端](04-desktop.md#3-生产构建)。

### 6.3 一键检查+构建

```powershell
.\start.bat build
```

包含 `env_doctor.ps1` 与 `check_frontend.ps1 -ForceBuild`；会安装/修复依赖，不是只读。

## 7. 环境变量

| 变量 | 作用 | 默认/注意 |
| --- | --- | --- |
| `HUYAN_NO_REAL` | 进程级禁真机 | 未设置；开发脚本默认 `1` |
| `HUYAN_TOKEN` | 非回环访问令牌 | 未设置时只允许本机 |
| `HUYAN_ALLOWED_HOSTS` | 额外允许的 Host（逗号分隔） | 默认空 |
| `ZHIPU_API_KEY` | AI 供应商密钥 | 只放本地 `.env`；也可面板运行时配置 |
| `ELFIN_DATA_DIR` | 数据目录覆盖 | 默认 `backend/data` |
| `ELFIN_DESKTOP_KEY` | 桌面私有 API 密钥 | 由 Electron 生成；缺省拒绝启动 |
| `ELFIN_DESKTOP` | 桌面进程标记 | 由 Electron 设置 |
| `ELFIN_VISION_PYTHON` | 视觉解释器路径 | 可选；缺失时用 `.venv-vision`/当前解释器 |
| `ELFIN_VISION_WORKER` | 视觉 worker 可执行文件 | 打包态指向 `ElfinVision.exe` |
| `ELFIN_REMOTE_ORIGIN` | 远程相机公开 HTTPS origin | 必须主机在允许列表；否则不显示入口 |
| `HUYAN_OFFLINE_ROOT` | 离线检查根 | 由运行器设置，禁止手工绕过 |
| `ELFIN_PLAY_CHECK_GROUP` 等 | 旧诊断分组变量 | 运行器会移除，避免悄悄缩减正式检查 |

密钥只放本地 `.env` 或运行时内存；不要写进源码、文档、日志、前端构建。

## 8. 端口、进程与日志

| 项 | 位置 |
| --- | --- |
| 5173 | Vite；进程核验 `node.exe` + `vite/bin/vite.js` |
| 8710 | 后端；进程核验 `.venv/Scripts/python.exe -m uvicorn backend.main:app --port 8710` |
| 桌面后端 | 随机端口；`desktop:listening` stdout 协议 |
| 开发日志 | `output/runtime/backend.log`、`backend.error.log`、`vite-dev.*.log` |
| 桌面日志 | `userData/logs/backend.log`（5MiB × 3 轮转） |
| 检查报告 | `output/checks/*.json` + 同名 `.log` |
| 构建证据 | `output/renewal/desktop-build-source.json`、`web/dist/build-identity.json` |
| 事件日志 | `backend/data/events.jsonl` |

## 9. 常见故障

| 现象 | 排查 |
| --- | --- |
| `No Python at ...` / venv 退出码 103 | `env_doctor.ps1`；不要只判断 python.exe 存在 |
| 5173 被占用 | `run_dev.ps1` 只复用能证明是本项目的 Vite；否则拒绝重复启动 |
| 8710 被无法核验进程占用 | `run_backend.ps1` 拒绝停止/重启；先查 PID/命令行 |
| 桌面提示 8710 纯 API | 先 `start.bat stop`，再重试桌面；或直接用 `start.bat dev` |
| 后端启动超时 | 看 `output/runtime/backend-launch-*.error.log`、`backend.log` |
| 页面空白/旧页面 | 重新 `npm --prefix web run build`；核对 `build-identity.json` |
| 状态条显示"状态已过期" | WS 断线/1.5 秒无帧；检查后端/代理/防火墙 |
| 相机页无设备 | 缺 SDK/权限/设备被占用；状态读取不会自动开设备 |
| 识别 0 个目标 | 检查检测是否显式开启、来源匹配、帧龄 ≤1.5 秒、ROI/颜色 |
| 预检慢 | 真实 IK/碰撞计算；看 STATUS 最新性能与 `check_start_latency.py` |
| 绘制/写字失败 | 工具/起点/场景/有效期绑定；重新预览，不要复用旧计划 |
| `docs.mjs check` 失败 | 看缺失链接/重复索引/归档哈希；逐项修，不删证据 |
| 离线检查失败 | 看 `output/checks/<name>.json` 与逐项 `.log`；不要只看末尾 PASS |

## 10. 开发流程建议

1. 读 [STATUS](../STATUS.md)、[TODO](../TODO.md) 和本 Wiki 的相关章节；
2. 定位调用链：路由 → 应用服务 → `World`/核心模块；
3. 先写最小失败用例（离线/前端/桌面），再改一个行为；
4. 跑定向检查 + 相邻回归；
5. 报告源码、离线、UI/GPU、现场四类结果，不混为"全部通过"；
6. 更新计划源与受影响文档；完成项退出 TODO，保留证据；
7. 不 reset/清理用户未提交改动；真机操作需单独授权。

## 11. 当前限制

- 真机、真实相机、实际 IO、外部 AI 质量、手机跨机 HTTPS 仍需现场验证；本 Wiki 不替代 [docs/STATUS.md](../STATUS.md)。
- MOVEL/MoveJTo/Servo 的现场资格依赖各自记录；代码存在不等于当前设备可用。
- 预检耗时仍是真实计算，可能达到数秒；不是"空等"。
- 任意 3D 网格/动画/贴图导入、YOLOE、完整工业安全认证均未交付。
