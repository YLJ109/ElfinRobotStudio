# 07 · 模块索引

按目录速查。每个文件一行职责；需要细节时回到 [02 后端](02-backend.md)、[03 前端](03-frontend.md)、[04 桌面端](04-desktop.md)。

## 1. 后端根目录

| 文件 | 职责 |
| --- | --- |
| [main.py](../../backend/main.py) | FastAPI 入口：依赖装配、访问守卫、全部根路由、WebSocket、lifespan |
| [bootstrap.py](../../backend/bootstrap.py) | `ApplicationResources`：World/相机/视觉/管线/租约/存储的启动与回滚 |
| [schemas.py](../../backend/schemas.py) | Pydantic 输入模型、设置校验、物体外观/变换校验 |
| [paths.py](../../backend/paths.py) | 项目根/数据目录/模型目录/Web 产物路径的唯一入口 |
| [build_identity.py](../../backend/build_identity.py) | 后端+契约源码 SHA256 与前端身份读取 |
| [access.py](../../backend/access.py) | Host 白名单、回环判断、远程访客只读路径 |
| [request_limits.py](../../backend/request_limits.py) | 请求体大小/接收超时限制（备份预检 32MiB 例外） |
| [task_routes.py](../../backend/task_routes.py) | 旧工艺录制只读兼容 |
| [teaching_routes.py](../../backend/teaching_routes.py) | 批量示教 |
| [workspace_routes.py](../../backend/workspace_routes.py) | 配置档案、场景模板/阵列/对齐/增删/撤销重做 |
| [play_routes.py](../../backend/play_routes.py) | 场景玩法事务（布置/重置/落子），不调用机器人 |
| [robot_play_routes.py](../../backend/robot_play_routes.py) | 取放唯一执行器、预检/运行/取消/释放 |
| [drawing_routes.py](../../backend/drawing_routes.py) | 绘制预览/运行/取消/暂停/继续 |
| [handling_challenge_routes.py](../../backend/handling_challenge_routes.py) | 搬运闯关 |
| [assembly_routes.py](../../backend/assembly_routes.py) | 装盒/拼摆 |
| [motion_session_routes.py](../../backend/motion_session_routes.py) | 单点/任务暂停/继续/取消 |
| [vision_routes.py](../../backend/vision_routes.py) | 只读视觉标记能力/检测 |
| [vision_workspace_routes.py](../../backend/vision_workspace_routes.py) | 视觉工作区状态/配置/标定/图像/虚拟帧 |
| [vision_pipeline_routes.py](../../backend/vision_pipeline_routes.py) | 手掌检测管线配置/帧 |
| [remote_camera_routes.py](../../backend/remote_camera_routes.py) | 远程相机配对/加入/心跳/撤销 |
| [scene_asset_routes.py](../../backend/scene_asset_routes.py) | 受控 GLB 检查/导入/下载 |
| [data_backup_routes.py](../../backend/data_backup_routes.py) | 备份导出/预检/恢复副本/激活计划 |
| [run_journal_routes.py](../../backend/run_journal_routes.py) | 运行记录列表/详情/导出 |
| [debug_routes.py](../../backend/debug_routes.py) | 旧程序调试接口（前端已无调用方） |
| [requirements.txt](../../backend/requirements.txt) | 运行时依赖锁定 |
| [requirements-vision*.txt](../../backend/requirements-vision.txt) | 视觉 worker 依赖/锁 |

## 2. 后端 core

| 文件 | 职责 |
| --- | --- |
| [world.py](../../backend/core/world.py) | 唯一状态源：50Hz 循环、运动/安全/工具/程序/真机/跟随/快照 |
| [kinematics.py](../../backend/core/kinematics.py) | `RobotModel`：FK、TCP、胶囊包络、IK、雅可比、距离 |
| [planning.py](../../backend/core/planning.py) | 路径采样/预检、MOVEL 曲线准备、计划摘要 |
| [trajectory.py](../../backend/core/trajectory.py) | `JointTrajectory`、`PreparedMotion`、时间参数化 |
| [safety.py](../../backend/core/safety.py) | 限位/围栏/地面/物体安全评估、接触档案、退离判据 |
| [motion.py](../../backend/core/motion.py) | `MotionExecutor`：唯一动作所有者、epoch 撤销 |
| [program.py](../../backend/core/program.py) | ElfinScript 解析/编译、`Runner` 步进器 |
| [python_program.py](../../backend/core/python_program.py) | 受限 Python AST 校验与协作解释器 |
| [program_store.py](../../backend/core/program_store.py) | 程序库 JSON 读写 |
| [robot_sim.py](../../backend/core/robot_sim.py) | `SimRobot`：关节插值、点动、暂停、倍率 |
| [objects.py](../../backend/core/objects.py) | `ObjectStore`：摆放物、重力/落地/碰撞代理 |
| [points.py](../../backend/core/points.py) | `PointStore`：点位、P 编号、来源 |
| [scenes.py](../../backend/core/scenes.py) | `SceneStore`：多现场布局文件 |
| [scene_assets.py](../../backend/core/scene_assets.py) | 受控 GLB 缓存/结构校验 |
| [tools.py](../../backend/core/tools.py) | `ToolStore`：工具档案、安装状态、动作映射 |
| [tool_library.py](../../backend/core/tool_library.py) | 只读工具目录与包络校验 |
| [sim_tool.py](../../backend/core/sim_tool.py) | 仿真末端模型/TCP/包络归一化 |
| [calibration.py](../../backend/core/calibration.py) | 工位/TCP 拟合与 `CalibrationStore` |
| [eventlog.py](../../backend/core/eventlog.py) | 环形事件日志、增量、CSV、清空 |
| [telemetry.py](../../backend/core/telemetry.py) | 回放缓冲、影子孪生偏差、就绪前体检 |
| [storage.py](../../backend/core/storage.py) | 原子 JSON 写入 |
| [service_lease.py](../../backend/core/service_lease.py) | 操作系统单控制进程锁 |
| [config.py](../../backend/core/config.py) | 默认设置、`.env`、设置持久化、速度键、AI 配置 |
| [controls.py](../../backend/core/controls.py) | 主按钮/FSM 推导、`motion_granted` |
| [protocol.py](../../backend/core/protocol.py) | 控制器报文编码/分帧/解析/状态解码 |
| [robot_real.py](../../backend/core/robot_real.py) | `RealLink`：真机命令/状态通道、回执、状态字段 |
| [real_follow_link.py](../../backend/core/real_follow_link.py) | `ServoCapableRealLink`、Servo 通道适配 |
| [real_servo.py](../../backend/core/real_servo.py) | `ServoProfile`、`RealServoSession` |
| [real_servo_transport.py](../../backend/core/real_servo_transport.py) | 独占 Servo 仲裁器/有界队列/隔离 |
| [real_follow_runtime.py](../../backend/core/real_follow_runtime.py) | `RealPalmFollowController`：真机跟随控制器 |
| [real_follow_host.py](../../backend/core/real_follow_host.py) | `RealPalmFollowHost`：跟随生命周期/停止确认 |
| [real_follow_startup.py](../../backend/core/real_follow_startup.py) | 先到中心、J6 归零启动流程 |
| [real_follow_finite.py](../../backend/core/real_follow_finite.py) | 有限中心运动执行器 |
| [real_palm_pipeline.py](../../backend/core/real_palm_pipeline.py) | 真机手掌目标限幅/最新值/时效 |
| [real_follow_qualification.py](../../backend/core/real_follow_qualification.py) | 跟随现场记录加载/匹配 |
| [real_movel_qualification.py](../../backend/core/real_movel_qualification.py) | MOVEL 现场记录加载/匹配 |
| [real_follow_tool_binding.py](../../backend/core/real_follow_tool_binding.py) | 真机工具/模型绑定与证据摘要 |
| [real_jog_follow.py](../../backend/core/real_jog_follow.py) | 真机点动跟随状态机 |
| [hand_follow.py](../../backend/core/hand_follow.py) | 仿真手掌跟随配置/启动/采样/IK/停止 |
| [hand_track.py](../../backend/core/hand_track.py) | 末端相机视觉伺服追踪状态机（范围盒模式会移动末端，无盒时纯朝向） |
| [hand_envelope.py](../../backend/core/hand_envelope.py) | 手部/工具/机器人包络计算 |
| [position_servo.py](../../backend/core/position_servo.py) | 位置伺服单步求解 |
| [ik_preview.py](../../backend/core/ik_preview.py) | 预览 IK 服务缓存 |
| [ik_reachability.py](../../backend/core/ik_reachability.py) | 可达性上界/审计 |
| [mesh_clearance.py](../../backend/core/mesh_clearance.py) | 网格顶点/最低点计算 |
| [play_vision.py](../../backend/core/play_vision.py) | 场景/视觉绑定、证据时效、分拣目标 |
| [pick_place_program.py](../../backend/core/pick_place_program.py) | 取放程序生成 |
| [task_studio.py](../../backend/core/task_studio.py) | 旧工艺录制模型 |
| [vision_workspace.py](../../backend/core/vision_workspace.py) | HSV/轮廓识别、ROI、标定、跟踪、多来源 |
| [cameras.py](../../backend/core/cameras.py) | RealSense/USB 采集、设备身份、最新帧、点云/测距 |

## 3. 后端 application / domain / infrastructure / routes

| 文件 | 职责 |
| --- | --- |
| [application/drawing.py](../../backend/application/drawing.py) | 绘制/写字计划：几何、预检、执行、暂停/继续 |
| [application/drawing_geometry.py](../../backend/application/drawing_geometry.py) | 画布配置、轨迹采样、边界 |
| [application/drawing_tcp.py](../../backend/application/drawing_tcp.py) | 绘制工具/TCP 绑定 |
| [application/text_writing.py](../../backend/application/text_writing.py) | 写字任务适配 |
| [application/assembly_tasks.py](../../backend/application/assembly_tasks.py) | 装盒/拼摆配方与格位 |
| [application/handling_challenge.py](../../backend/application/handling_challenge.py) | 搬运闯关布置/计分 |
| [application/sorting_score.py](../../backend/application/sorting_score.py) | 分拣计分 |
| [application/vision_tasks.py](../../backend/application/vision_tasks.py) | 找物/计数/尺寸定位只读任务 |
| [application/vision_pipeline.py](../../backend/application/vision_pipeline.py) | 最新帧检测管线、模型预热 |
| [application/remote_camera.py](../../backend/application/remote_camera.py) | 远程相机配对/WebRTC 生命周期 |
| [application/remote_clock.py](../../backend/application/remote_clock.py) | 远程心跳时钟估计（不替代采集时间） |
| [application/run_journal.py](../../backend/application/run_journal.py) | 运行记录观察/脱敏/有界存储 |
| [application/data_backup.py](../../backend/application/data_backup.py) | 备份导出/校验/恢复副本 |
| [application/acceptance.py](../../backend/application/acceptance.py) | 验收记录读取/与当前身份比对 |
| [application/ai_clarification.py](../../backend/application/ai_clarification.py) | AI 澄清补充请求 |
| [application/ai_skills.py](../../backend/application/ai_skills.py) | AI 组合技能（绘制/接近）复用唯一执行器 |
| [domain/motion_target.py](../../backend/domain/motion_target.py) | 运动目标/到达证据/轴门禁 |
| [domain/object_contacts.py](../../backend/domain/object_contacts.py) | 物体接触/承托校验 |
| [domain/scene_bounds.py](../../backend/domain/scene_bounds.py) | 场景边界契约 |
| [domain/suction.py](../../backend/domain/suction.py) | 吸盘侧别/动作/IO 映射校验 |
| [domain/tool_actions.py](../../backend/domain/tool_actions.py) | 工具动作名称/别名 |
| [domain/handling_challenge.py](../../backend/domain/handling_challenge.py) | 闯关关卡/目标/计分模型 |
| [infrastructure/vision/process_detector.py](../../backend/infrastructure/vision/process_detector.py) | 独立进程手掌检测 IPC |
| [infrastructure/vision/hand_worker.py](../../backend/infrastructure/vision/hand_worker.py) | MediaPipe worker 主循环 |
| [routes/vision_tasks.py](../../backend/routes/vision_tasks.py) | 视觉任务路由工厂 |

## 4. 前端根目录

| 文件 | 职责 |
| --- | --- |
| [main.js](../../web/src/main.js) | 主应用入口：Pinia、桌面桥、标签/折叠 |
| [App.vue](../../web/src/App.vue) | 应用外壳：导航、侧栏、三维舞台、状态条、场景装载 |
| [theme.css](../../web/src/theme.css) | 全局主题/控件/动效样式 |
| [store.js](../../web/src/store.js) | Pinia `world`：WS、资源、动作、日志、AI |
| [connectionState.js](../../web/src/connectionState.js) | WS 重连、HTTP 解析、六轴/显示/速度真实性 |
| [targetState.js](../../web/src/targetState.js) | 单会话目标状态机、失败去重、暂停/继续 |
| [workspaceState.js](../../web/src/workspaceState.js) | 偏好/标签状态/面板折叠滚动持久化 |
| [workspaceInsights.js](../../web/src/workspaceInsights.js) | 设置改动预览等摘要 |
| [runtimeMetrics.js](../../web/src/runtimeMetrics.js) | 前端性能指标窗口 |
| [acceptanceView.js](../../web/src/acceptanceView.js) | 验收记录展示投影 |
| [desktopStartup.js](../../web/src/desktopStartup.js) | 桌面启动桥接/恢复 |
| [tabNavigation.js](../../web/src/tabNavigation.js) | 键盘标签导航 |
| [panelFolding.js](../../web/src/panelFolding.js) | 面板折叠行为 |
| [cameraSource.js](../../web/src/cameraSource.js) | 真实/虚拟/浏览器相机来源编排 |
| [browserCameraPreview.ts](../../web/src/browserCameraPreview.ts) | 浏览器相机预览/轨道停止 |
| [virtualCamera.js](../../web/src/virtualCamera.js) | 末端虚拟相机取景/截图 |
| [virtualCameraMount.js](../../web/src/virtualCameraMount.js) | 虚拟相机安装默认值 |
| [cameraFence.js](../../web/src/cameraFence.js) | 相机范围/围栏显示辅助 |
| [visionWorkspace.js](../../web/src/visionWorkspace.js) | 视觉工作区响应式状态/轮询/虚拟采集 |
| [visionMarkers.js](../../web/src/visionMarkers.js) | 标记文件读取 |
| [visionMain.js](../../web/src/visionMain.js) | 独立视觉入口 |
| [VisionStation.vue](../../web/src/VisionStation.vue) | 独立视觉窗口页面 |
| [remoteCamera.ts](../../web/src/remoteCamera.ts) | 远程访客 WebRTC/心跳 |
| [remoteCameraMain.ts](../../web/src/remoteCameraMain.ts) | 远程访客入口 |
| [RemoteCameraStation.vue](../../web/src/RemoteCameraStation.vue) | 远程访客页面 |
| [handFollowInput.js](../../web/src/handFollowInput.js) | 手掌输入、帧龄、平滑、跟随客户端 |
| [handPipeline.js](../../web/src/handPipeline.js) | 浏览器侧检测帧解析/状态 |
| [handGeometry.js](../../web/src/handGeometry.js) | 手掌几何/范围换算 |
| [handDisplayHold.js](../../web/src/handDisplayHold.js) | 显示保持（不延长采集时效） |
| [mouseFollowInput.js](../../web/src/mouseFollowInput.js) | 鼠标模拟输入 |
| [interactionCore.js](../../web/src/interactionCore.js) | 交互输入共同核心 |
| [interactionInput.js](../../web/src/interactionInput.js) | 输入源选择/校验（被多个检查引用） |
| [interactionRange.js](../../web/src/interactionRange.js) | 交互范围默认/中心旋转 |
| [logFollow.js](../../web/src/logFollow.js) | 日志跟随/高亮 |
| [logSummary.js](../../web/src/logSummary.js) | 日志摘要 |
| [scene.js](../../web/src/scene.js) | Three.js 场景与全部渲染 API |
| [sceneTools.js](../../web/src/sceneTools.js) | 场景模板/阵列/对齐/撤销重做 |
| [sceneObjectEditing.js](../../web/src/sceneObjectEditing.js) | 物体编辑请求归属 |
| [sceneAssetVisual.js](../../web/src/sceneAssetVisual.js) | 导入 GLB 可视化解析 |
| [robotSurface.js](../../web/src/robotSurface.js) | 机器人表面材质 |
| [robotSurfaceAsset.js](../../web/src/robotSurfaceAsset.js) | 表面增强资产加载 |
| [jointCaps.js](../../web/src/jointCaps.js) | 关节灯带/端盖 |
| [jointPresentation.js](../../web/src/jointPresentation.js) | 关节插值/展示 |
| [toolVisual.js](../../web/src/toolVisual.js) | 吸盘/笔/工具库模型与动画 |
| [toolKinematics.js](../../web/src/toolKinematics.js) | 工具几何/TCP 换算 |
| [visualAssets.js](../../web/src/visualAssets.js) | 型号/工具/皮肤资产白名单 |
| [drawingVisual.js](../../web/src/drawingVisual.js) | 笔迹/浅浮雕/路径可视化 |
| [drawingPreview.js](../../web/src/drawingPreview.js) | 本地图形预览 |
| [drawingTask.js](../../web/src/drawingTask.js) | 绘制/写字控制器 |
| [writingConfig.js](../../web/src/writingConfig.js) | 写字默认文字/地面映射/样式 |
| [textContours.js](../../web/src/textContours.js) | 本地字形轮廓 |
| [textDraft.js](../../web/src/textDraft.js) | 文字草稿 |
| [textWritingView.js](../../web/src/textWritingView.js) | 写字视图辅助 |
| [textWorker.js](../../web/src/textWorker.js) | 字形 worker：调用 `rasterText` 并回传结果 |
| [objectCatalog.js](../../web/src/objectCatalog.js) | 物体预设/材质/缩略图 |
| [objectGround.js](../../web/src/objectGround.js) | 物体落地高度 |
| [objectTeaching.js](../../web/src/objectTeaching.js) | 物体拖动示教 |
| [groundDirections.js](../../web/src/groundDirections.js) | 地面方向标签 |
| [suctionControls.js](../../web/src/suctionControls.js) | 吸盘按钮状态 |
| [simTaskPen.js](../../web/src/simTaskPen.js) | 仿真任务自动换笔并等待工具确认 |
| [chessMatch.js](../../web/src/chessMatch.js) | 井字棋回合状态机 |
| [functionPlay.js](../../web/src/functionPlay.js) | 场景玩法控制器 |
| [practicalTasks.js](../../web/src/practicalTasks.js) | 找物/计数/装盒/拼摆控制器 |
| [robotPlayProgram.js](../../web/src/robotPlayProgram.js) | 取放任务控制器 |
| [aiConversation.js](../../web/src/aiConversation.js) | AI 对话/计划/撤销 |
| [aiProviderCatalog.js](../../web/src/aiProviderCatalog.js) | AI 供应商/模型目录 |
| [aiSkillProjection.js](../../web/src/aiSkillProjection.js) | AI 组合技能展示投影 |
| [teachingDraft.js](../../web/src/teachingDraft.js) | 示教草稿/上下文 |
| [programLanguage.js](../../web/src/programLanguage.js) | 中英文语法词表/翻译 |
| [programDiagnostics.js](../../web/src/programDiagnostics.js) | 程序诊断展示 |
| [operationNotes.js](../../web/src/operationNotes.js) | 操作说明/禁止事项 |
| [performanceComparison.js](../../web/src/performanceComparison.js) | 性能前后采样 |
| [taskFrontView.js](../../web/src/taskFrontView.js) | 任务前台视图辅助 |
| [domain/readiness.js](../../web/src/domain/readiness.js) | 任务/启动条件纯投影 |
| [shared/toolActions.ts](../../web/src/shared/toolActions.ts) | 工具动作共享类型/常量 |

## 5. 前端 panels

| 文件 | 职责 |
| --- | --- |
| [ControlPanel.vue](../../web/src/panels/ControlPanel.vue) | 控制页分类容器 |
| [SimPanel.vue](../../web/src/panels/SimPanel.vue) | 仿真运行/运动学 |
| [PowerPanel.vue](../../web/src/panels/PowerPanel.vue) | 连接/电源/就绪/手拖/诊断 |
| [TeachingPanel.vue](../../web/src/panels/TeachingPanel.vue) | 关节/笛卡尔/三维示教 |
| [PointsPanel.vue](../../web/src/panels/PointsPanel.vue) | 点位执行/示教/吸盘 |
| [ProgramPanel.vue](../../web/src/panels/ProgramPanel.vue) | 程序编辑/库/语法/运行 |
| [AiPanel.vue](../../web/src/panels/AiPanel.vue) | AI 工作页包装 |
| [LegacyAiAssistant.vue](../../web/src/panels/LegacyAiAssistant.vue) | 单助手/输入行/计划确认 |
| [ObjectsPanel.vue](../../web/src/panels/ObjectsPanel.vue) | 物体库/场景物体/现场文件 |
| [FunctionsPanel.vue](../../web/src/panels/FunctionsPanel.vue) | 有趣/分拣子页 |
| [HandFollowPanel.vue](../../web/src/panels/HandFollowPanel.vue) | 手掌/鼠标跟随配置 |
| [VisionPanel.vue](../../web/src/panels/VisionPanel.vue) | 摄像头/识别/找物/计数/标定 |
| [VisionMarkerPanel.vue](../../web/src/panels/VisionMarkerPanel.vue) | 标记/标定辅助 |
| [FencePanel.vue](../../web/src/panels/FencePanel.vue) | 围栏/阈值/播报/残影透明度 |
| [SystemPanel.vue](../../web/src/panels/SystemPanel.vue) | 系统页标签容器 |
| [EngineeringPanel.vue](../../web/src/panels/EngineeringPanel.vue) | 轨迹/标定/IO/末端/诊断 |
| [LogsPanel.vue](../../web/src/panels/LogsPanel.vue) | 日志 |
| [TelemetryPanel.vue](../../web/src/panels/TelemetryPanel.vue) | 健康/趋势/IO/回放/性能 |
| [SettingsPanel.vue](../../web/src/panels/SettingsPanel.vue) | 设置/档案/备份 |
| [AboutPanel.vue](../../web/src/panels/AboutPanel.vue) | 概览/指南/字段/验证 |
| [WorkPanel.vue](../../web/src/panels/WorkPanel.vue) | 点位/程序/AI 容器 |

## 6. 前端 components

| 文件 | 职责 |
| --- | --- |
| [TopBar.vue](../../web/src/components/TopBar.vue) | 顶栏模式/速度/连接/主操作 |
| [NavRail.vue](../../web/src/components/NavRail.vue) | 左侧导航 |
| [StatusStrip.vue](../../web/src/components/StatusStrip.vue) | 底栏状态/进度 |
| [TaskTabs.vue](../../web/src/components/TaskTabs.vue) | 页内标签 |
| [CameraWidget.vue](../../web/src/components/CameraWidget.vue) | 浮动相机窗口 |
| [CameraFrame.vue](../../web/src/components/CameraFrame.vue) | 图像/检测框显示 |
| [CameraSettings.vue](../../web/src/components/CameraSettings.vue) | 设备/流配置 |
| [CameraCloud.vue](../../web/src/components/CameraCloud.vue) | 点云 |
| [BrowserCameraPreview.vue](../../web/src/components/BrowserCameraPreview.vue) | 浏览器相机预览 |
| [ModelLibrary.vue](../../web/src/components/ModelLibrary.vue) | 模型/工具/皮肤选择 |
| [ObjectProps.vue](../../web/src/components/ObjectProps.vue) | 物体属性编辑 |
| [ProgramEditor.vue](../../web/src/components/ProgramEditor.vue) | CodeMirror 编辑器 |
| [ProgramRules.vue](../../web/src/components/ProgramRules.vue) | 语法说明 |
| [RobotPlayProgram.vue](../../web/src/components/RobotPlayProgram.vue) | 取放任务 UI |
| [SuctionControls.vue](../../web/src/components/SuctionControls.vue) | 吸盘四按钮 |
| [DrawingTask.vue](../../web/src/components/DrawingTask.vue) | 绘制任务 UI |
| [TextWritingTask.vue](../../web/src/components/TextWritingTask.vue) | 写字任务 UI |
| [HandlingChallenge.vue](../../web/src/components/HandlingChallenge.vue) | 搬运闯关 UI |
| [PracticalTask.vue](../../web/src/components/PracticalTask.vue) | 找物/计数/装盒/拼摆 UI |
| [SceneTools.vue](../../web/src/components/SceneTools.vue) | 场景布局工具 |
| [SceneAssetImport.vue](../../web/src/components/SceneAssetImport.vue) | GLB 导入 |
| [TrackPanel.vue](../../web/src/components/TrackPanel.vue) | 追踪页 |
| [RunJournal.vue](../../web/src/components/RunJournal.vue) | 运行记录 |
| [LogDock.vue](../../web/src/components/LogDock.vue) | 日志浮层 |
| [DataBackup.vue](../../web/src/components/DataBackup.vue) | 备份/恢复 |
| [RemoteCameraPairing.vue](../../web/src/components/RemoteCameraPairing.vue) | 远程配对 |
| [VisionObjectOverlay.vue](../../web/src/components/VisionObjectOverlay.vue) | 视觉对象叠加 |
| [ReadyControl.vue](../../web/src/components/ReadyControl.vue) | 就绪控件 |
| [Spark.vue](../../web/src/components/Spark.vue) | 迷你曲线 |
| [ThemeSelectLayer.vue](../../web/src/components/ThemeSelectLayer.vue) | 主题选择 |
| [AiModelConfig.vue](../../web/src/components/AiModelConfig.vue) | AI 供应商/模型配置 |

## 7. 桌面端

| 文件 | 职责 |
| --- | --- |
| [main.cjs](../../desktop/main.cjs) | Electron 主进程、窗口、IPC、生命周期 |
| [preload.cjs](../../desktop/preload.cjs) | 受限渲染器 API |
| [backend.cjs](../../desktop/backend.cjs) | 后端会话启动/复用/健康/关闭 |
| [backend_entry.py](../../desktop/backend_entry.py) | 私有后端入口/桌面 key/静态页挂载 |
| [buildIdentity.cjs](../../desktop/buildIdentity.cjs) | 源码/前端身份兼容 |
| [dataSelection.cjs](../../desktop/dataSelection.cjs) | 数据目录选择/恢复清单校验 |
| [recovery.cjs](../../desktop/recovery.cjs) | 恢复副本激活/重启 |
| [cameraPermission.cjs](../../desktop/cameraPermission.cjs) | 相机权限仅限自有窗口 |
| [contentCache.cjs](../../desktop/contentCache.cjs) | 清缓存不影响用户数据 |
| [logSink.cjs](../../desktop/logSink.cjs) | 有界轮转日志 |
| [smoke.cjs](../../desktop/smoke.cjs) | 冒烟驱动/截图 |
| [qualitySmoke.cjs](../../desktop/qualitySmoke.cjs) | 画质冒烟 |
| [recoverySmoke.cjs](../../desktop/recoverySmoke.cjs) | 恢复冒烟 |
| [renewalSmoke.cjs](../../desktop/renewalSmoke.cjs) | 翻新验收冒烟 |
| [drawingSmoke.cjs](../../desktop/drawingSmoke.cjs) | 绘制冒烟 |
| [playSmoke.cjs](../../desktop/playSmoke.cjs) | 场景玩法冒烟 |
| [robotPlaySmoke.cjs](../../desktop/robotPlaySmoke.cjs) | 取放冒烟 |
| [practicalSmoke.cjs](../../desktop/practicalSmoke.cjs) | 实用任务冒烟 |
| [visionSmoke.cjs](../../desktop/visionSmoke.cjs) | 视觉冒烟 |
| [sortingVisionSmoke.cjs](../../desktop/sortingVisionSmoke.cjs) | 分拣视觉冒烟 |
| [entryTruthSmoke.cjs](../../desktop/entryTruthSmoke.cjs) | 入口真实性 |
| [userFeedbackSmoke.cjs](../../desktop/userFeedbackSmoke.cjs) | 反馈回归 |
| [handlingFeedbackSmoke.cjs](../../desktop/handlingFeedbackSmoke.cjs) | 搬运反馈 |
| [aiClarificationSmoke.cjs](../../desktop/aiClarificationSmoke.cjs) | AI 澄清 |
| [journalUiSmoke.cjs](../../desktop/journalUiSmoke.cjs) | 运行记录 UI |
| [performanceSmoke.cjs](../../desktop/performanceSmoke.cjs) | 性能冒烟 |
| [reviewSmoke.cjs](../../desktop/reviewSmoke.cjs) | 评审冒烟 |
| [backend.spec](../../desktop/backend.spec) | PyInstaller 后端打包 |
| [vision_worker.spec](../../desktop/vision_worker.spec) | PyInstaller 视觉 worker |
| [test/](../../desktop/test) | 14 个桌面单元测试 |

## 8. 工具脚本（按用途分组）

| 组 | 脚本 |
| --- | --- |
| 环境/启动 | [env_doctor.ps1](../../tools/env_doctor.ps1)、[run_dev.ps1](../../tools/run_dev.ps1)、[run_backend.ps1](../../tools/run_backend.ps1)、[wait_backend.ps1](../../tools/wait_backend.ps1)、[check_frontend.ps1](../../tools/check_frontend.ps1)、[check_project.ps1](../../tools/check_project.ps1) |
| 检查运行器 | [run_offline_checks.py](../../tools/run_offline_checks.py)、[offline_checks.json](../../tools/offline_checks.json)、[_offline_runtime.py](../../tools/_offline_runtime.py)、[_offline_guard.py](../../tools/_offline_guard.py) |
| 运动/安全 | `check_ik*.py`、`check_fk.py`、`check_movej_allowed_axes.py`、`check_full_link_collision.py`、`check_collision_recede.py`、`check_safety_domains.py`、`check_motion_*.py`、`check_real_movel.py` |
| 跟随/视觉 | `check_hand_*.py`、`check_follow_*.py`、`check_vision_*.py`、`check_camera_*.py`、`check_remote_*.py` |
| 任务/程序 | `check_drawing*.py`、`check_text_writing.py`、`check_play_*.py`、`check_robot_play.py`、`check_handling_*.py`、`check_assembly_tasks.py`、`check_program_*.py`、`check_python_program.py` |
| 数据/安全边界 | `check_workspace_routes.py`、`check_scene_*.py`、`check_data_backup*.py`、`check_run_journal.py`、`check_offline_isolation.py`、`security_check.py` |
| 性能/诊断 | [check_start_latency.py](../../tools/check_start_latency.py)、[check_play_preflight_profile.py](../../tools/check_play_preflight_profile.py)、`prof_*.py`、`perf_probe.py`、`diagnose_hand_pipeline.py` |
| 浏览器/截图 | [qa_cdp.py](../../tools/qa_cdp.py)、[qa_first_move.py](../../tools/qa_first_move.py)、`cdp_*.mjs`、`dom_audit.mjs`、`shot_cdp.mjs` |
| 演示/数据安装 | `install_demo_points.py`、`install_demo_programs.py`、`install_demo_scenes.py`、`build_robot_model.py`、`build_tool_assets.py` |
| 真机诊断（非离线） | `connect_robot.ps1`、`scan_robot.py`、`io_probe.py`、`power_probe.py`、`real_ready_cycle.py`、`smoke_cmd_channel.py`、`dump_io.py` |

> 真机诊断脚本不属于离线检查清单；`run_offline_checks.py` 会拒绝运行未列入清单的脚本。

## 9. 资产与契约

| 路径 | 内容 |
| --- | --- |
| [web/public/models/elfin5](../../web/public/models/elfin5) | `robot.json`、各 link STL/GLB、低模、表面增强、LICENSE |
| `web/public/models/elfin10* / elfin15 / elfin3 / elfin5_l` | 其它型号资产；当前 UI 只开放 elfin5 |
| [web/public/tools/catalog.json](../../web/public/tools/catalog.json) | 本地工具库目录/包络/动画 |
| [web/public/tools](../../web/public/tools) | 工具 GLB（Robotiq/SCHUNK/Franka 等） |
| [web/public/vision](../../web/public/vision) | `hand_landmarker.task`、MediaPipe 许可、来源 |
| [contracts/tool-actions.json](../../contracts/tool-actions.json) | 工具动作契约 |
| [contracts/scene_bounds.json](../../contracts/scene_bounds.json) | 场景边界契约 |
| [contracts/remote-camera.json](../../contracts/remote-camera.json) | 远程相机契约 |
