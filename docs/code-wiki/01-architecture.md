# 01 · 总体架构

## 1. 项目定位

玖枢（目录名 `ElfinRobotStudio`）是一个**本地六轴机器人工作站**，把以下能力放在同一个界面里：

- 六轴机器人三维仿真、关节/笛卡尔示教、点位与程序执行；
- 场景摆放物、围栏、碰撞/地面安全余量；
- 相机采集、工件识别、手掌跟随、远程只读视觉；
- 受约束的真机接入（连接、就绪、点动、IO、Servo 跟随）；
- 仿真取放/分拣/井字棋/装盒/拼摆/绘制/写字等任务；
- 数据备份、运行记录、遥测、日志和验收证据。

它不是任意品牌机器人的通用驱动，也不是已通过现场认证的工业控制产品。代码里存在的真机通信能力不等于当前设备可通、可动或已验收。

来源：[README.md](../../README.md)、[docs/PROJECT.md](../PROJECT.md)、[docs/STATUS.md](../STATUS.md)。

## 2. 技术栈

| 层 | 技术 | 版本/位置 |
| --- | --- | --- |
| 后端 | Python 3.11+、FastAPI、Uvicorn、Pydantic、httpx | [pyproject.toml](../../pyproject.toml)、[backend/requirements.txt](../../backend/requirements.txt) |
| 前端 | Vue 3、Pinia、Vite 6、Three.js 0.170、CodeMirror 6 | [web/package.json](../../web/package.json) |
| 桌面 | Electron 45、electron-builder 26 | [package.json](../../package.json) |
| 视觉推理 | MediaPipe 手掌模型，独立 `.venv-vision` / `ElfinVision.exe` | [backend/infrastructure/vision](../../backend/infrastructure/vision) |
| 打包 | PyInstaller（后端与视觉 worker）+ electron-builder（NSIS/dir） | [desktop/backend.spec](../../desktop/backend.spec)、[desktop/vision_worker.spec](../../desktop/vision_worker.spec)、[tools/build_desktop.py](../../tools/build_desktop.py) |
| 数据 | 本地 JSON 文件 + JSONL 事件日志 | `backend/data/`，可用 `ELFIN_DATA_DIR` 覆盖 |

## 3. 运行时拓扑

```text
┌─────────────────────────────────────────────────────────────────────┐
│ 浏览器（开发/生产页面）                                             │
│  http://localhost:5173/  ── Vite 代理 /api /ws ──┐                  │
│  独立入口 /vision.html（视觉工作站）              │                  │
│  独立入口 /remote-camera.html（访客远程相机）      │                  │
└───────────────────────────────────────────────────┼──────────────────┘
                                                    │ HTTP + WebSocket
┌───────────────────────────────────────────────────▼──────────────────┐
│ FastAPI 后端（backend.main:app）                                     │
│  REST 命令入口 / WS 状态推送                                          │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │ World（唯一状态源）                                             │  │
│  │  50Hz 主循环（跟随 60Hz）                                       │  │
│  │  SimRobot / Runner / MotionExecutor / 安全评估 / 遥测            │  │
│  └───────┬───────────────┬───────────────┬────────────────┬───────┘  │
│          │               │               │                │          │
│   RealLink(真机)     CameraManager    VisionWorkspace   RobotPlay    │
│   10003/10004        USB/RealSense     HSV/轮廓识别      取放任务     │
│          │               │               │                │          │
│          │               │        VisionPipeline ──独立进程──▶ 手掌检测 │
│          │               │               │                │          │
│          └───────────────┴───────────────┴────────────────┘          │
│  数据目录 backend/data（settings/points/objects/scenes/programs/...） │
└──────────────────────────────────────────────────────────────────────┘
```

桌面形态不是"再复制一份网页"，而是：

```text
Electron 主进程 (desktop/main.cjs)
  ├─ 启动私有后端：打包态 ElfinBackend.exe / 源码态 backend/.venv python backend_entry.py
  │    └─ 监听 127.0.0.1 随机端口；桌面专属状态/关闭接口要求 x-elfin-desktop-key
  ├─ 私有后端额外挂载 web/dist 页面，主窗口加载该地址
  ├─ 需要时打开独立视觉窗口 /vision.html
  └─ 关闭时先走受控停止，再退出后端
```

关键事实：

- 开发入口只有 **5173**；8710 **只提供 API**，`GET /` 返回 404 JSON（[backend/main.py](../../backend/main.py#L1296-L1306)）。
- 桌面私有后端使用**随机端口**并在进程内挂载 `web/dist`（[desktop/backend_entry.py](../../desktop/backend_entry.py#L27-L72)）。
- 视觉检测在独立进程中运行，不与 FastAPI 事件循环共享 MediaPipe（[backend/infrastructure/vision/process_detector.py](../../backend/infrastructure/vision/process_detector.py#L15-L54)）。
- 真机协议按项目文档为命令 10003、状态 10004；未做其他品牌适配（[docs/PROJECT.md](../PROJECT.md)）。

## 4. 代码分层

### 4.1 后端

| 层 | 目录 | 职责 | 入口 |
| --- | --- | --- | --- |
| 组装/入口 | `backend/` 根 | 创建 FastAPI、装配资源、安全中间件、WS | [main.py](../../backend/main.py#L82) |
| 路由 | `backend/*_routes.py`、`backend/routes/` | 只做参数校验、调用服务、返回 JSON | 各 `create_*_router()` |
| 应用服务 | `backend/application/` | 有状态任务编排：绘制、装盒、搬运、视觉管线、备份、运行记录、远程相机 | 见 [02 后端](02-backend.md) |
| 核心领域 | `backend/core/` | `World`、运动学、规划、安全、程序、工具、物体、相机、真机链路 | [world.py](../../backend/core/world.py#L67) |
| 领域契约 | `backend/domain/` | 与框架无关的小模型：运动目标、接触、吸盘、场景边界、工具动作 | `domain/*.py` |
| 基础设施 | `backend/infrastructure/vision/` | 手掌检测 worker 与进程 IPC | `hand_worker.py`、`process_detector.py` |
| 路径/身份 | `backend/paths.py`、`build_identity.py` | 数据目录、模型目录、源码/前端指纹 | [paths.py](../../backend/paths.py)、[build_identity.py](../../backend/build_identity.py) |

装配顺序在 [main.py](../../backend/main.py#L68-L205)：

1. `build_dependencies()` 创建 `World`、`CameraManager`、`VisionWorkspace`、`VisionPipeline`、`ServiceLease`、`ProgramStore`、`SceneStore`、`PreviewIKService`；
2. `create_app()` 注册 GZip、请求体大小限制、访问守卫、异常处理；
3. 依次挂载视觉、工作区、任务、点位、程序、遥测、AI、真机等路由；
4. `lifespan` 启动资源并预热检测模型，关闭时先收绘制、再收相机/世界/租约。

`ApplicationResources` 是资源生命周期的唯一所有者（[bootstrap.py](../../backend/bootstrap.py#L5-L57)），路由不持有设备生命周期。

### 4.2 前端

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 入口 | [main.js](../../web/src/main.js) | 安装 Pinia、桌面桥接、标签导航、面板折叠，挂载 `App.vue` |
| 外壳 | [App.vue](../../web/src/App.vue) | 顶栏/导航/侧栏/三维舞台/状态条；按 `view` 切换面板 |
| 全局状态 | [store.js](../../web/src/store.js#L143-L573) | Pinia `world` store：WS 快照、资源加载、REST 动作、日志、AI |
| 纯领域逻辑 | `web/src/*.js` | 目标状态机、连接、相机源、跟随输入、绘制/棋局/任务控制器 |
| 三维 | [scene.js](../../web/src/scene.js#L46) | Three.js 场景、机器人、围栏、残影、物体、视觉对象、绘制可视化 |
| UI 面板 | `web/src/panels/` | 控制、点位、程序、AI、场景、视觉、有趣/分拣、系统 |
| 组件 | `web/src/components/` | 相机、吸盘、模型库、程序编辑器、任务子页、状态条 |
| 独立入口 | `web/src/visionMain.js`、`remoteCameraMain.ts` | 视觉工作站、远程访客页，不启动主 App/三维/World |

前端有两条状态通道：

- **3D 快通道**：WS 原始帧先进入模块级变量 `latest`，由 `setFrameHandler` 直接喂给 Three.js，不进入 Vue 响应式（[store.js](../../web/src/store.js#L57-L65)）。
- **UI 慢通道**：同一帧按 50ms 节流写入 `snap`（20Hz），遥测只在 `telemetry_seq` 变化时替换（[store.js](../../web/src/store.js#L247-L307)）。

### 4.3 桌面

Electron 只负责窗口、进程、权限与启动编排，不复制业务逻辑：

- 主进程 [desktop/main.cjs](../../desktop/main.cjs)：创建主窗口/视觉窗口、校验 IPC 来源、关闭流程；
- 后端会话 [desktop/backend.cjs](../../desktop/backend.cjs#L18)：优先复用兼容的 8710 服务，否则拉起私有后端并解析 `desktop-listening` 端口；
- 页面桥 [desktop/preload.cjs](../../desktop/preload.cjs)：只暴露窗口控制、视觉窗口、启动重试、恢复选择等有限 API；
- 私有后端 [desktop/backend_entry.py](../../desktop/backend_entry.py)：校验桌面 key、提供 `/api/desktop/status|shutdown`、挂载 `web/dist`。

## 5. 唯一状态源：World

`World` 是仿真/监控系统的单一状态源（[world.py](../../backend/core/world.py#L67)）。它持有：

- 机器人模型 `RobotModel`、仿真对象 `SimRobot`；
- 点位、物体、工具、标定、场景（场景由外层 `SceneStore` 管理）；
- 程序 `Runner`、运动执行器 `MotionExecutor`；
- 预览目标、运动目标、到达证据、执行上下文；
- 安全评估结果、碰撞命中、围栏/地面状态；
- 真机链路 `RealLink`、真机跟随控制器；
- 仿真/真机 IO、工具动作、吸盘状态；
- 日志 `EventLog`、遥测 `Playback`、趋势缓冲、黑匣子。

### 5.1 主循环

[World._loop](../../backend/core/world.py#L381-L415)：

- 基础周期 0.02s（50Hz）；手掌跟随激活时 0.0167s（60Hz）；
- 每 tick 推进仿真、运动执行队列、安全评估、遥测落格；
- 广播周期：静止 20Hz、运动 30Hz、跟随 60Hz；
- 订阅队列 `maxsize=1`，慢客户端只拿最新帧，不积压；
- 单帧异常只记日志，不拖死循环。

### 5.2 快照分两条通道

`World.snapshot()`（[world.py](../../backend/core/world.py#L4059-L4141)）返回：

| 通道 | 内容 | 频率 |
| --- | --- | --- |
| 快通道 | 关节、TCP、安全、运动、执行、工具、物体、权限、预览、日志摘要 | 每帧 |
| 遥测 | 温度/电流/力/IO/脚本/设备身份、偏差、体检、回放索引、健康摘要 | 1Hz（`force_tele` 例外） |

前端依赖这些字段做控制使能、残影、安全条、日志、遥测页；新增字段时要先判断它是否真的需要每帧发送。

### 5.3 指令与状态分离

- REST 只接受"意图"（预览、移动、执行、停止、设置），返回结果或拒绝原因；
- WS 只推送"观察"（状态快照、日志增量、ping）；
- 没有任何 REST 接口直接写仿真关节或真机状态；所有变化都经过 `World` 的方法。

## 6. 运动链路

```text
点位/程序/AI/绘制/取放
        │
        ▼
  预览（preview）── 生成残影与 preview_safety，不运动
        │  用户确认/执行
        ▼
  plan_context 摘要（session/mode/config/executor epoch/tool/TCP）
        │
        ▼
  MotionExecutor（唯一运动所有者，epoch 可撤销）
        │
        ├─ 仿真：SimRobot/JointTrajectory（关节插值、MOVEL 曲线）
        └─ 真机：RealLink（MoveRelJ 逐轴 / 受资格约束的 MOVEL / Servo 跟随）
        │
        ▼
  安全评估 → 告警/暂停/停止 → 到达证据（不是 ACK）
```

关键实现：

- `World.register_motion_target()` / `execute_motion_target()`（[world.py](../../backend/core/world.py#L714-L765)）把目标与 `plan_id` 绑定，过期或上下文变化即失效。
- `World.inspect_plan()` / `execute_plan()`（[world.py](../../backend/core/world.py#L2496-L2520)）是外部"先检查后执行"的统一入口。
- `planning.inspect_path()` 做关节路径采样、限位/围栏/地面/物体检查、可退离判定（[planning.py](../../backend/core/planning.py#L69-L203)）。
- `planning.prepare_movel()` 生成 C1 关节曲线与预检报告（[planning.py](../../backend/core/planning.py#L204)）。
- `MotionExecutor` 用 `epoch` 撤销已提交动作；取消后旧结果不能再报告完成（[motion.py](../../backend/core/motion.py#L8-L71)）。

**不变量**：预览不是命令；ACK 不是到位；停止请求不是已停稳。未知反馈不补零（[world.py](../../backend/core/world.py#L417-L435)）。

## 7. 视觉链路

```text
USB/RealSense 相机 ── CameraManager ── 最新帧槽（服务端时间戳、会话号）
        │
        ├─ VisionWorkspace（HSV/轮廓、ROI、标定、跟踪 ID、虚拟/远程来源）
        │       └─ 找物/计数/分拣/装盒/拼摆读取同一快照
        │
        └─ VisionPipeline（最新帧、TTL 0.3s）── 独立进程 ── MediaPipe 手掌检测
                ├─ 仿真手掌跟随（World.HandFollow）
                ├─ 真实手掌跟随（RealPalmFollowController/Host）
                └─ 远程相机（单向 WebRTC，只读）
```

- `CameraManager` 只做采集与缓存，不在状态读取时打开设备（[cameras.py](../../backend/core/cameras.py#L15)）。
- `VisionWorkspace` 只读相机、估测平面、临时跟踪，不引用机器人或持久场景（[vision_workspace.py](../../backend/core/vision_workspace.py#L60)）。
- `VisionPipeline` 用"一个最新帧槽 + 工作线程"避免积压；检测结果有 TTL 与会话/序号校验（[vision_pipeline.py](../../backend/application/vision_pipeline.py#L11-L50)）。
- 远程相机只传视频，不授予机器人权限；visitor 页面只读找物/计数（[remote_camera.py](../../backend/application/remote_camera.py#L19)、[contracts/remote-camera.json](../../contracts/remote-camera.json)）。

## 8. 任务编排

| 服务 | 角色 | 关键约束 |
| --- | --- | --- |
| `PlayService` | 场景玩法事务：布置/重置/落子，不调用机器人 | 只改仿真道具，先落盘再发布内存（[play_routes.py](../../backend/play_routes.py#L58-L74)） |
| `RobotPlay` | 取放唯一执行器：预检→计划→复用 Runner/MotionExecutor→吸放 | 不拥有真机通道；持件、接触 ID、视觉绑定都绑定计划上下文（[robot_play_routes.py](../../backend/robot_play_routes.py#L39)） |
| `Drawing` | 绘制/写字：几何→IK→曲线→预检→执行 | 复用同一执行器；计划有 TTL、起点/配置/工具/场景绑定（[application/drawing.py](../../backend/application/drawing.py#L21)） |
| `AssemblyTasks` | 装盒/拼摆：配方/像素格位 | 只允许有限格位、颜色、容量，未知/缺料不执行（[application/assembly_tasks.py](../../backend/application/assembly_tasks.py#L46)） |
| `HandlingChallenge` | 搬运闯关：关卡布置与基于证据的计分 | 计分看实际位置，不看动画（[application/handling_challenge.py](../../backend/application/handling_challenge.py#L13)） |
| `Runner` + `Interpreter` | 程序执行：ElfinScript 行指令或受限 Python 子集 | 解释器不执行任意代码；程序由 World tick 推进（[program.py](../../backend/core/program.py#L355)、[python_program.py](../../backend/core/python_program.py#L85)） |

## 9. 安全与"真相"不变量

这些是代码里真实存在的门禁，改架构时不能拆：

1. **控制权唯一**：`World` + `MotionExecutor` + 一个 `ServiceLease` 锁；禁止多进程同时控制（[service_lease.py](../../backend/core/service_lease.py#L6)）。
2. **真机资格从控制器状态推导**：FSM=33 且 enabled=1 才算 `motion_granted`；软件开关不再单独授予许可（[controls.py](../../backend/core/controls.py#L33-L80)）。
3. **计划绑定上下文**：`plan_context`/`digest` 绑定会话、模式、配置、执行器 epoch、工具/TCP、场景；上下文变化旧计划失效。
4. **安全分级**：关节/围栏/地面/物体 90% 黄、95% 红、100% 告警；接触/硬边界告警并停止（[safety.py](../../backend/core/safety.py#L1-L19)）。
5. **碰撞口径分离**：围栏/地面看末端工具；摆放物看全部可动连杆，排除固定底座（[safety.py](../../backend/core/safety.py#L60-L75)）。
6. **未知即未知**：控制器没给的字段保持 `None`，前端显示 `--`；仿真值必须标 `sim`（[telemetry.py](../../backend/core/telemetry.py#L1-L8)）。
7. **访问边界**：默认只认回环地址；非本机需要 `HUYAN_TOKEN`；写请求校验 Origin；Host 需在允许列表（[backend/main.py](../../backend/main.py#L110-L161)、[access.py](../../backend/access.py#L7-L22)）。
8. **隔离闸**：`HUYAN_NO_REAL=1` 在进程层面禁止连接真机，供检查/演练使用（[config.py](../../backend/core/config.py#L242-L249)）。
9. **停止事务**：`stop_execution()` 发停止、等待反馈证明停稳；不把"已请求"当"已停稳"（[world.py](../../backend/core/world.py#L2541-L2606)）。
10. **数据先落盘再发布**：`write_json` 原子替换；场景/点位/程序写入失败不留半套状态（[storage.py](../../backend/core/storage.py#L8-L20)）。

## 10. 关键数据流

| 场景 | 写入方 | 经过 | 读取方 |
| --- | --- | --- | --- |
| 关节/真机状态 | `RealLink` 或 `SimRobot` | `World._tick` → `snapshot` → WS | 前端 store、三维、状态条 |
| 预览/残影 | `PointsPanel`/目标状态机 | `POST /api/preview` → `World` → 快照 `preview` | Three.js 残影、`preview_safety` |
| 程序执行 | 程序页/AI/任务 | `Runner` → `World._run_step` → `MotionExecutor` | 执行状态、运行记录、日志 |
| 取放 | 分拣/井盒/拼摆/棋局 | `RobotPlay.generate` → `run` → `Runner` | 物体位置、持件、计分 |
| 视觉识别 | 相机/虚拟/远程 | `VisionWorkspace` 快照 | 找物/计数/任务、三维叠加 |
| 手掌跟随 | 相机/鼠标 | `VisionPipeline`/鼠标输入 → `HandFollow`/`RealPalmFollowController` | 仿真关节、真机 Servo 计划 |
| 日志 | 各模块 | `EventLog` 环形缓冲 + JSONL | WS `log_delta`、日志页、CSV 导出 |

## 11. 扩展点与不推荐的改法

**推荐**：

- 新任务：在 `application/` 建服务，复用 `RobotPlay`/`Runner`/`MotionExecutor`，只加路由薄层；
- 新视觉来源：扩展 `VisionWorkspace.config.input` 与 `cameraSource`，保持"最新帧 + 时效 + 来源身份"；
- 新工具：走 `ToolStore`/`tool_library` 档案，不直接写三维模型；
- 新页面：在 `NAV_VIEWS`/`PANELS` 注册，面板只读 store，不自己建 WS。

**不要**：

- 绕过 `World` 直接改 `SimRobot.q` 或 `RealLink`；
- 在路由里创建第二个 `World`/`MotionExecutor`/`WebSocket`；
- 把预览写成"已执行"，把 ACK 写成"已到位"；
- 在 UI 里猜一个默认值填未知反馈；
- 让远程访客入口获得控制 API。
