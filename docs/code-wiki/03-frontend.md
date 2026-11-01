# 03 · 前端

前端是 Vue 3 + Pinia + Three.js 的单页应用，源码在 `web/`。它同时承载主工作站页面、独立视觉工作站页和远程访客页。

- 入口：[web/src/main.js](../../web/src/main.js)
- 外壳：[web/src/App.vue](../../web/src/App.vue)
- 全局 store：[web/src/store.js](../../web/src/store.js#L143)
- 三维场景：[web/src/scene.js](../../web/src/scene.js#L46)

## 1. 启动与入口

### 1.1 三个 HTML 入口

| 入口 | JS | 说明 |
| --- | --- | --- |
| [index.html](../../web/index.html) | [main.js](../../web/src/main.js) | 主工作站：导航、三维、面板 |
| [vision.html](../../web/vision.html) | [visionMain.js](../../web/src/visionMain.js) | 独立视觉窗口：只有 `VisionStation.vue`，不启动主 App/World/三维 |
| [remote-camera.html](../../web/remote-camera.html) | [remoteCameraMain.ts](../../web/src/remoteCameraMain.ts) | 远程访客页：只读视频/识别结果，无控制权限 |

`vite.config.js`（[web/vite.config.js](../../web/vite.config.js)）把 `/api`、`/ws` 代理到 8710，并在代理层只为 5173 开发来源改写 Origin；生产构建由 [build-production.mjs](../../web/scripts/build-production.mjs) 校验三个入口不依赖开发站点。

### 1.2 主 App 启动顺序

[main.js](../../web/src/main.js)：

1. `installTabNavigation(document)`：键盘/标签页行为；
2. 创建 Pinia；
3. `createDesktopStartup({ bridge, world, watch })`：桌面端启动/恢复桥接；
4. 挂载 `App.vue`；
5. `installPanelFolding(document)`：面板折叠持久化。

`App.vue` 挂载后：

- 读取 URL hash 和本地偏好，得到 `view/workTab/systemTab/controlTab`；
- `loadVisualModel()` 异步创建 Three.js 场景，先验证候选场景 `prepareFirstFrame()`，再释放旧场景；
- `setFrameHandler(feed3d)` 把 WS 原始帧直接喂给三维；
- 订阅 `w.snap` 把工具、安全、物体、残影、轨迹、健康推给场景；
- 卸载时断开 WS、释放场景、保存面板状态。

## 2. 导航与页面结构

### 2.1 导航常量

[store.js](../../web/src/store.js#L27-L29)：

```js
SYSTEM_TABS = ['engineering', 'logs', 'tele', 'settings', 'about']
WORK_TABS   = ['points', 'program', 'ai']
NAV_VIEWS   = ['control', 'work', 'objects', 'engineering',
               'hand-follow', 'sorting', 'fence', 'vision']
```

`normalizeNavigation()`（[store.js](../../web/src/store.js#L32)）兼容旧书签：`sim/power/drive → control`，`studio → work/program`，旧系统页 → `engineering`。

### 2.2 左侧导航

[NavRail.vue](../../web/src/components/NavRail.vue#L11-L22)：

| 入口 | view | workTab | 作用 |
| --- | --- | --- | --- |
| 控制 | `control` | - | 运行/连接/运动学/使能控制/诊断 |
| 点位 | `work` | `points` | 点位执行、示教、吸盘 |
| 有趣 | `hand-follow` | - | 跟随/追踪/井字棋/绘制/写字 |
| 分拣 | `sorting` | - | 分拣/搬运闯关/装盒/拼摆 |
| 视觉 | `vision` | - | 摄像头、识别、找物、计数、尺寸定位、标定 |
| 程序 | `work` | `program` | 编辑器、程序库、语法 |
| AI | `work` | `ai` | 单一助手、计划确认 |
| 场景 | `objects` | - | 物体库、场景物体、现场文件 |
| 围栏 | `fence` | - | 范围、检测、阈值、播报 |
| 系统 | `engineering` | - | 工具/日志/遥测/设置/关于 |

### 2.3 页面映射

`App.vue` 的 `PANELS`（[App.vue](../../web/src/App.vue#L247-L249)）：

| view | 面板 |
| --- | --- |
| `control` | [ControlPanel.vue](../../web/src/panels/ControlPanel.vue) |
| `work` | [WorkPanel.vue](../../web/src/panels/WorkPanel.vue) |
| `objects` | [ObjectsPanel.vue](../../web/src/panels/ObjectsPanel.vue) |
| `engineering` | [SystemPanel.vue](../../web/src/panels/SystemPanel.vue) |
| `fence` | [FencePanel.vue](../../web/src/panels/FencePanel.vue) |
| `hand-follow` / `sorting` | [FunctionsPanel.vue](../../web/src/panels/FunctionsPanel.vue)，用 `category` 区分 |
| `vision` | [VisionPanel.vue](../../web/src/panels/VisionPanel.vue) |

工作页子页：`WorkPanel.vue`（[L12](../../web/src/panels/WorkPanel.vue#L12)）映射 `points → PointsPanel`、`program → ProgramPanel`、`ai → AiPanel`、`engineering → SystemPanel`。

系统页子页：`SystemPanel.vue`（[L11-L17](../../web/src/panels/SystemPanel.vue#L11-L17)）映射 `engineering/logs/tele/settings/about`，首次访问才挂载，之后保活。

控制页子页：`ControlPanel.vue` 用 `SimPanel`（运行/运动学）与 `PowerPanel`（连接/使能控制/诊断）切换；真机模式才显示"使能控制"。

### 2.4 有趣/分拣子页

`FunctionsPanel.vue`（[L22-L24](../../web/src/panels/FunctionsPanel.vue#L22-L24)）：

- 有趣：`interaction`（跟随）、`track`（追踪）、`chess`（井字棋）、`drawing`（绘制）、`writing`（写字）；
- 分拣：`sorting`（分拣）、`handling`（搬运闯关）、`kit`（配套装盒）、`pattern`（图案拼摆）。

进入跟随/井字棋/分拣页会自动确保仿真摄像头（`ensureVirtualVision()`），绘制/写字不自动开相机，避免采集与路径预检抢事件循环。

## 3. 全局状态：Pinia `world`

[store.js](../../web/src/store.js#L143-L573) 的 state 主要字段：

| 字段 | 含义 |
| --- | --- |
| `snap` | 20Hz UI 快照（关节、安全、TCP、执行、工具、物体、设置、日志） |
| `tele` | 1Hz 遥测（温度/电流/力/IO/脚本/健康/回放） |
| `meta` | 模型、命令、语法、AI/限位/电源能力 |
| `points/programs/scenes/tools/cameras/logs` | 启动资源 |
| `connected/stateFresh` | WS 连接与 1.5 秒时效 |
| `view/workTab/systemTab/controlTab` | 导航状态 |
| `preview` | 后端登记的残影目标 |
| `targetState` | 会话内目标状态机（不持久化） |
| `aiHistory/aiPlan/aiPending/aiExecuting` | AI 对话与计划 |
| `play` | 时间轴回放数据 |
| `startLatency` | 最近一次"点击→首次关节变化"实测 |
| `error/connectionError/frameError/resourceErrors` | 可见错误 |

### 3.1 双通道

- WS 原始帧先写模块级 `latest`，`frameCb` 直接喂 Three.js；
- UI 每 50ms 才写 `snap`；
- 遥测只在 `telemetry_seq` 变化时替换；
- 日志用 `log_delta` 增量，空增量保留同一响应式数组；
- 状态帧 1.5 秒不来就 `stateFresh=false`，目标状态机失效（[L247-L307](../../web/src/store.js#L247-L307)）。

### 3.2 启动资源

`STARTUP_RESOURCES`（[L106-L110](../../web/src/store.js#L106-L110)）：

```text
/api/meta, /api/points, /api/programs, /api/tools, /api/cameras, /api/scenes
```

`boot()` 先 `connectWs()`，再并行加载资源；单个资源失败只记录 `resourceErrors`，不阻塞状态通道。

### 3.3 请求归属与错误

`ownedRequest()`（[L76](../../web/src/store.js#L76)）给同一 `scope` 的请求发版本号：旧响应不能覆盖新结果，旧错误不能覆盖新错误。页面切换/取消导致的迟到响应会被丢弃。

### 3.4 主要 action 分组

| 组 | 代表方法 |
| --- | --- |
| 连接 | `boot`、`retryResources`、`connectWs`、`disconnectWs` |
| 通用 | `call(path, body)`、`setSpeed`、`setSettings` |
| 控制 | `power`、`control`、`free`、`readySequence`、`estop` |
| 运动 | `jog`、`moveJ`、`previewRequest`、`moveTargetRequest`、`ik`、`fk`、`setPreview`、`cancelPreview` |
| 点位 | `teach`、`refreshPoints`、`addPoint`、`removePoint`、`renamePoint`、`runPoints` |
| 程序 | `loadProgram`、`startProgram`、`saveProgram`、`deleteProgram`、`openProgram` |
| 场景 | `addObject`、`placeObject`、`editObject`、`removeObject`、`saveScene`、`loadScene`、`importScene`、`exportScene` |
| 工具 | `selectTool`、`clearTool`、`toolAction`、`refreshTools` |
| 相机 | `cameraState`、`refreshCameras`、`openCamera`、`closeCamera` |
| AI | `aiSay`、`aiRun`、`aiReason`、`aiClarificationReason`、`aiDiscard` |
| 回放 | `playLoad`、`playSeek`、`playFrame` |

## 4. 状态与领域工具模块

| 模块 | 位置 | 职责 |
| --- | --- | --- |
| `createStateConnection` | [connectionState.js](../../web/src/connectionState.js#L2) | WS 自动重连、停止、消息回调；不创建浏览器全局状态 |
| `parseResponse` | [connectionState.js](../../web/src/connectionState.js#L54) | 统一 HTTP 错误解析 |
| `isSixAxis` / `displayJoints` | [isSixAxis](../../web/src/connectionState.js#L72)、[displayJoints](../../web/src/connectionState.js#L79) | 六轴有限值校验；真机无有效反馈返回 `null`，不补零 |
| `speedState` | [connectionState.js](../../web/src/connectionState.js#L88) | "保存的目标倍率"与"控制器实际倍率"分离 |
| `targetFor` | [targetState.js](../../web/src/targetState.js#L43) | 单会话目标状态机：预览/可执行/失效/执行/失败；失败按 run 身份去重 |
| `taskProjection` / `readinessProjection` | [taskProjection](../../web/src/domain/readiness.js#L2)、[readinessProjection](../../web/src/domain/readiness.js#L18) | 纯展示投影：当前任务、启动条件、未知状态 |
| `readPreference` / `savePreference` | [readPreference](../../web/src/workspaceState.js#L4)、[savePreference](../../web/src/workspaceState.js#L10) | 会话/本地偏好读写（不含控制令牌） |
| `createWorkspaceUiState` | [workspaceState.js](../../web/src/workspaceState.js#L31) | 面板折叠/滚动位置按页面持久化 |
| `normalizeNavigation` | [store.js](../../web/src/store.js#L32) | 旧路由兼容 |

### 4.1 目标状态机

[targetState.js](../../web/src/targetState.js) 的核心：

- `targetContext(world)`（[L5](../../web/src/targetState.js#L5)）把连接、会话、模式、真实链路、配置版本、TCP、工具/可视化绑定成字符串；上下文变化即失效。
- `targetFor(world)`（[L43](../../web/src/targetState.js#L43)）为每个 store 维护唯一控制器；状态字段包括 `revision/stage/context/joints/planId/pose/reason/pending/acknowledged/executing/executionOutcome/failure`。
- `invalidate()`（[L95](../../web/src/targetState.js#L95)）撤销目标、丢弃 pending、清到达证据；旧执行结果不能写回新目标。
- 暂停/继续只通过可验证的 `motionStatus` 与 token 路径，界面不"假停"。

## 5. 三维场景

### 5.1 创建

`createScene(container, options)`（[scene.js](../../web/src/scene.js#L46)）：

- 从 `visualAssets.robotAsset(model)` 取模型路径，加载 `robot.json`；
- 加载各 link 的 STL/GLB（完整/低模/表面增强），构建关节层级；
- 创建相机、灯光、地面、网格、围栏、工具、残影、物体、视觉对象、绘制可视化；
- `prepareFirstFrame()` 必须确认所有模型资源存在并完成首帧编译，缺模型不能以空场景冒充就绪（[L1470-L1482](../../web/src/scene.js#L1470-L1482)）；
- `dispose()` 释放几何/材质/纹理/控制器/事件监听。

### 5.2 返回 API

`createScene` 返回的 API（[L1456-L1693](../../web/src/scene.js#L1456-L1693)）按用途分组：

| 分组 | 方法 |
| --- | --- |
| 机器人姿态 | `setAngles`、`setImmediate`、`fitView`、`resetView`、`getView`、`setView`、`onViewChange`、`poseNow` |
| 围栏/安全 | `setFence`、`setFenceSafety`、`setFenceVisible`、`setSafetyThresholds`、`setGroundSafety`、`setCapsules`、`markCollisions`、`setHealth` |
| 残影/回放 | `setGhost`、`clearGhosts`、`ghostInfo`、`ghostOpacity` |
| 物体 | `setObjects`、`setObjectTarget`、`beginPlacement`、`cancelPlacement`、`finishObjectMove`、`cancelObjectMove`、`placementInfo` |
| 工具 | `setToolVisual`、`toolInfo`、`setToolState`、`setTcpFrame` |
| 跟随 | `setHandWorkspace`、`setHandBoxPreview`、`fitHandWorkspace`、`handWorkspaceInfo`、`setFollowLatency` |
| 视觉 | `setVisionObjects`、`configureVirtualCamera`、`captureVirtualFrame`、`virtualCameraPose` |
| 绘制 | `setDrawingBounds`、`setDrawingState`、`clearDrawing`、`drawingInfo`、`setPlanPath`、`clearPlanPath` |
| 追踪 | `setTrackWorkspace`、`setTrackHand` |
| 历史/性能 | `clearTrail`、`exportHistory`、`restoreHistory`、`setTrailLen`、`resourceInfo`、`dprInfo`、`resize`、`dispose` |

三维层不计算权限、不做 IK、不判断到位；它只渲染后端给的姿态、安全、残影和计划路径。

### 5.3 资源与皮肤

- [visualAssets.js](../../web/src/visualAssets.js)：型号白名单（当前只保留 elfin5）、工具库资产、默认吸盘尺寸。
- [toolVisual.js](../../web/src/toolVisual.js)：吸盘参考模型、笔、工具库 GLB、动作动画。
- [jointCaps.js](../../web/src/jointCaps.js)、[robotSurface.js](../../web/src/robotSurface.js)、[robotSurfaceAsset.js](../../web/src/robotSurfaceAsset.js)：关节灯带、表面材质、表面增强资产。
- [objectCatalog.js](../../web/src/objectCatalog.js)、[objectGround.js](../../web/src/objectGround.js)：物体预设/材质/缩略图、落地高度。
- [drawingVisual.js](../../web/src/drawingVisual.js)：笔迹逐步出现、浅浮雕、路径。
- [virtualCamera.js](../../web/src/virtualCamera.js)：末端虚拟相机取景与截图。

## 6. 前端任务控制器

这些模块把"页面按钮"变成"有状态、可取消、可恢复"的控制器，页面只渲染状态。

| 控制器 | 位置 | 职责 |
| --- | --- | --- |
| `createDrawingTask` | [drawingTask.js](../../web/src/drawingTask.js#L99) | 绘制/写字预览、运行、暂停/继续/取消；计划绑定与迟到响应清理 |
| `createChessMatch` | [chessMatch.js](../../web/src/chessMatch.js#L20) | 井字棋回合：检查场景、求解、提交、机器人落子、人类落子 |
| `createPlayController` / `playFor` | [createPlayController](../../web/src/functionPlay.js#L79)、[playFor](../../web/src/functionPlay.js#L117) | 场景玩法 setup/reset/落子，互斥与错误 |
| `createPracticalTask` | [practicalTasks.js](../../web/src/practicalTasks.js#L20) | 找物/计数/装盒/拼摆的请求与确认流程 |
| `createRobotPlayController` | [robotPlayProgram.js](../../web/src/robotPlayProgram.js#L34) | 取放预检/运行/停止/释放，轮询与计划失效 |
| `aiFor` | [aiConversation.js](../../web/src/aiConversation.js#L13) | AI 对话/计划/确认/撤销；上下文或反馈变化即失效 |
| `createCameraSource` | [cameraSource.js](../../web/src/cameraSource.js#L12) | 真实相机、虚拟相机、浏览器相机来源选择与准备 |
| `createHandFollowClient` | [handFollowInput.js](../../web/src/handFollowInput.js#L256) | 手掌跟随配置/启动/停止、帧龄检查、迟到启用回滚 |
| `createPalmTracker` | [handFollowInput.js](../../web/src/handFollowInput.js#L117) | 手掌像素/深度平滑与显示保持 |
| `selectHandFollowSnapshot` | [handFollowInput.js](../../web/src/handFollowInput.js#L250) | 仿真/真机跟随快照选择 |
| `handPipeline` | [handPipeline.js](../../web/src/handPipeline.js) | 浏览器侧帧解析/检测结果状态 |
| `visionWorkspace` | [visionWorkspace.js](../../web/src/visionWorkspace.js#L4) | 视觉工作区响应式状态、轮询、虚拟采集、对象映射 |
| `createPerformanceComparison` | [performanceComparison.js](../../web/src/performanceComparison.js) | 性能采样前后对比、版本/视口身份 |
| `createObjectTeaching` | [objectTeaching.js](../../web/src/objectTeaching.js) | 场景物体拖动示教与提交 |
| `createSceneTools` | [sceneTools.js](../../web/src/sceneTools.js) | 场景模板/阵列/对齐/撤销重做调用 |
| `createLogFollow` | [logFollow.js](../../web/src/logFollow.js) | 日志跟随/高亮/导出摘要 |
| `createWorkspaceUiState` | [workspaceState.js](../../web/src/workspaceState.js#L31) | 面板折叠/滚动持久化 |

## 7. 面板职责

| 面板 | 文件 | 职责 |
| --- | --- | --- |
| `ControlPanel` | [ControlPanel.vue](../../web/src/panels/ControlPanel.vue) | 运行/连接/运动学/使能控制/诊断分类 |
| `SimPanel` | [SimPanel.vue](../../web/src/panels/SimPanel.vue) | 仿真运行、关节/笛卡尔、状态 |
| `PowerPanel` | [PowerPanel.vue](../../web/src/panels/PowerPanel.vue) | 连接、上电/就绪/使能/下电/停止、手拖 |
| `TeachingPanel` | [TeachingPanel.vue](../../web/src/panels/TeachingPanel.vue) | 点动/笛卡尔/三维示教、目标采集 |
| `PointsPanel` | [PointsPanel.vue](../../web/src/panels/PointsPanel.vue) | 点位保存/执行/示教/吸盘，残影与到达证据 |
| `ProgramPanel` | [ProgramPanel.vue](../../web/src/panels/ProgramPanel.vue) | 编辑器/程序库/语法，校验/运行/停止/暂停 |
| `AiPanel` | [AiPanel.vue](../../web/src/panels/AiPanel.vue) | 包装 `LegacyAiAssistant`，单助手与计划确认 |
| `ObjectsPanel` | [ObjectsPanel.vue](../../web/src/panels/ObjectsPanel.vue) | 物体库/场景物体/现场文件，GLB 导入 |
| `FunctionsPanel` | [FunctionsPanel.vue](../../web/src/panels/FunctionsPanel.vue) | 有趣/分拣子页编排 |
| `HandFollowPanel` | [HandFollowPanel.vue](../../web/src/panels/HandFollowPanel.vue) | 手掌/鼠标输入、交互范围、四朝向、中心、启动/停止 |
| `VisionPanel` | [VisionPanel.vue](../../web/src/panels/VisionPanel.vue) | 摄像头/工件识别/找物/计数/尺寸定位/标定 |
| `VisionMarkerPanel` | [VisionMarkerPanel.vue](../../web/src/panels/VisionMarkerPanel.vue) | 标记点/相机标定辅助 |
| `FencePanel` | [FencePanel.vue](../../web/src/panels/FencePanel.vue) | 围栏范围、检测开关、阈值、播报、残影透明度 |
| `SystemPanel` | [SystemPanel.vue](../../web/src/panels/SystemPanel.vue) | 系统页标签容器 |
| `EngineeringPanel` | [EngineeringPanel.vue](../../web/src/panels/EngineeringPanel.vue) | 轨迹/标定/IO/末端/诊断/显示 |
| `LogsPanel` | [LogsPanel.vue](../../web/src/panels/LogsPanel.vue) | 日志过滤/跟随/CSV 导出 |
| `TelemetryPanel` | [TelemetryPanel.vue](../../web/src/panels/TelemetryPanel.vue) | 健康/趋势/IO/回放/性能 |
| `SettingsPanel` | [SettingsPanel.vue](../../web/src/panels/SettingsPanel.vue) | 连接/显示/高级/改动预览/配置档案/备份 |
| `AboutPanel` | [AboutPanel.vue](../../web/src/panels/AboutPanel.vue) | 概览/指南/字段复核/更新验证 |
| `WorkPanel` | [WorkPanel.vue](../../web/src/panels/WorkPanel.vue) | 点位/程序/AI 子页容器 |

## 8. 关键组件

| 组件 | 文件 | 职责 |
| --- | --- | --- |
| `TopBar` | [TopBar.vue](../../web/src/components/TopBar.vue) | 模式、速度、连接、主操作 |
| `NavRail` | [NavRail.vue](../../web/src/components/NavRail.vue) | 左侧导航 |
| `StatusStrip` | [StatusStrip.vue](../../web/src/components/StatusStrip.vue) | 底栏：控制方、阶段、安全、进度 |
| `TaskTabs` | [TaskTabs.vue](../../web/src/components/TaskTabs.vue) | 页内标签 |
| `CameraWidget` / `CameraFrame` / `CameraSettings` | [CameraWidget.vue](../../web/src/components/CameraWidget.vue)、[CameraFrame.vue](../../web/src/components/CameraFrame.vue)、[CameraSettings.vue](../../web/src/components/CameraSettings.vue) | 相机窗口、帧显示、设备/流设置 |
| `CameraCloud` | [CameraCloud.vue](../../web/src/components/CameraCloud.vue) | 点云显示 |
| `ModelLibrary` | [ModelLibrary.vue](../../web/src/components/ModelLibrary.vue) | 机器人/工具/皮肤选择 |
| `ObjectProps` | [ObjectProps.vue](../../web/src/components/ObjectProps.vue) | 选中物体的位姿/尺寸/材质/锁定 |
| `ProgramEditor` / `ProgramRules` | [ProgramEditor.vue](../../web/src/components/ProgramEditor.vue)、[ProgramRules.vue](../../web/src/components/ProgramRules.vue) | CodeMirror 编辑器、语法说明 |
| `RobotPlayProgram` | [RobotPlayProgram.vue](../../web/src/components/RobotPlayProgram.vue) | 取放任务控制 |
| `SuctionControls` | [SuctionControls.vue](../../web/src/components/SuctionControls.vue) | 左右吸盘四按钮/状态 |
| `DrawingTask` / `TextWritingTask` | [DrawingTask.vue](../../web/src/components/DrawingTask.vue)、[TextWritingTask.vue](../../web/src/components/TextWritingTask.vue) | 绘制/写字表单、预览、执行 |
| `HandlingChallenge` / `PracticalTask` | [HandlingChallenge.vue](../../web/src/components/HandlingChallenge.vue)、[PracticalTask.vue](../../web/src/components/PracticalTask.vue) | 闯关、找物/计数/装盒/拼摆 |
| `SceneTools` / `SceneAssetImport` | [SceneTools.vue](../../web/src/components/SceneTools.vue)、[SceneAssetImport.vue](../../web/src/components/SceneAssetImport.vue) | 场景布局/撤销、GLB 导入 |
| `RunJournal` / `LogDock` / `DataBackup` | [RunJournal.vue](../../web/src/components/RunJournal.vue)、[LogDock.vue](../../web/src/components/LogDock.vue)、[DataBackup.vue](../../web/src/components/DataBackup.vue) | 运行记录、日志浮层、备份恢复 |
| `TrackPanel` | [TrackPanel.vue](../../web/src/components/TrackPanel.vue) | 末端相机视觉伺服追踪：范围盒、模拟/摄像头输入、去中心 |
| `ThemeSelectLayer` | [ThemeSelectLayer.vue](../../web/src/components/ThemeSelectLayer.vue) | 主题选择 |

## 9. 视觉工作站与远程访客

### 9.1 独立视觉窗口

[VisionStation.vue](../../web/src/VisionStation.vue)：

- 只挂载 `VisionPanel` + 识别叠加 + 摄像头轮询；
- 使用 `window.elfinDesktop` 的窗口控制/最大化；
- 不启动主 App、三维场景或 World；
- 适合在桌面端单独放在副屏。

### 9.2 远程访客

[remoteCamera.ts](../../web/src/remoteCamera.ts) + [remote-camera.html](../../web/remote-camera.html)：

- 配对码加入 → 发送只读视频 SDP → 心跳；
- 页面只显示视频/识别结果，不提供控制按钮；
- 体积/带宽/时长受限，单独打包到 `assets/remote-camera/`（[vite.remote.config.js](../../web/vite.remote.config.js)）；
- 契约见 [contracts/remote-camera.json](../../contracts/remote-camera.json)。

## 10. 前端检查

`npm --prefix web test` 运行 `web/scripts/check-*.mjs`（97 个脚本），覆盖：

- store/WS/连接/导航/目标状态；
- 相机来源、手掌跟随、视觉工作区；
- 绘制/写字/棋局/任务/程序编辑器；
- 三维场景初始化/资源/性能/皮肤；
- UI 文案/标签/无障碍/响应式；
- 桌面启动桥接与生产入口校验。

这些是**离线/构建级检查**，不等于浏览器逐页点击、GPU 画面或真机验收。当前通过数量与失败记录以 [STATUS](../STATUS.md) 为准。

## 11. 前端约定

- 页面只通过 `store` 的 action 发请求；不要在每个组件里新建 WebSocket。
- 请求必须能被取消或按 scope 归属；迟到响应不能覆盖新状态。
- 任何"目标"都要带上下文；`targetContext` 变了就重新预览。
- 真机反馈缺失时显示"未知/--"，不要用仿真 0 位或默认值。
- 三维动画只消费后端状态，不在前端伪造到达/碰撞/安全。
- 新页面先注册 `NAV_VIEWS`/`PANELS`/hash 兼容，避免旧书签失效。
