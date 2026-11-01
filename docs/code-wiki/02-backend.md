# 02 · 后端

后端是 Python/FastAPI 应用，代码位于 `backend/`。它的核心不是"路由集合"，而是 **一个 `World` 状态源 + 一组围绕它的领域模块 + 薄路由层**。

- 入口：[backend/main.py](../../backend/main.py)
- 状态源：[backend/core/world.py](../../backend/core/world.py#L67)
- 资源生命周期：[backend/bootstrap.py](../../backend/bootstrap.py#L5)

## 1. 入口与装配

### 1.1 `create_app()`

[backend/main.py](../../backend/main.py#L82) 做四件事：

1. 接收或构造 `ApplicationResources`；
2. 创建 `FastAPI`，注册 `GZipMiddleware`、`RequestSizeLimit`、全局 `ValueError → 422`；
3. 注册访问守卫：Host 白名单、非本机 token、写请求 Origin 校验、API `no-store` 响应头；
4. 挂载所有 `create_*_router()`，最后暴露 `app.state.handlers` 供检查脚本直接调用。

`build_dependencies()`（[main.py](../../backend/main.py#L68)）显式构造依赖：

```text
World(data_dir, SettingsStore)
CameraManager
VisionWorkspace(camera, vision_workspace.json)
VisionPipeline(camera, ProcessHandDetector)
ServiceLease(data/control.lock)
ProgramStore(programs.json)
SceneStore(scenes.json, sanitizer=World.objects.sanitize)
PreviewIKService()
```

`ApplicationResources.start()`（[bootstrap.py](../../backend/bootstrap.py#L14)）先拿租约，再启动运行记录、World、视觉、检测管线、远程相机；任何一步失败都先回滚已启动资源，再抛出原始异常。

### 1.2 `lifespan`

- 启动：[main.py](../../backend/main.py#L207-L220)，启动资源并后台预热 MediaPipe（约 8–10 秒）。
- 关闭：[main.py](../../backend/main.py#L223-L228)，先关绘制子系统，再关资源（远程相机 → World → 运行记录 → 检测管线 → 视觉 → 相机 → 租约）。

### 1.3 WebSocket

[main.py](../../backend/main.py#L1244-L1293)：

- 先校验 Host、Origin、非本机 token；
- 首帧强制带遥测；
- 之后从 `World.subscribe()` 的唯一槽取快照；
- `settings` 只在 `config_rev` 变化时发送；
- `log` 改为增量 `log_delta`；
- 30 秒无新帧发 `ping`。

## 2. `World`：唯一状态源

`World.__init__`（[world.py](../../backend/core/world.py#L67-L188)）创建/持有：

| 类别 | 成员 |
| --- | --- |
| 模型/仿真 | `RobotModel("elfin5")`、`SimRobot`、`_vmax`、限位 |
| 数据 | `EventLog`、`PointStore`、`ObjectStore`、`ToolStore`、`CalibrationStore` |
| 执行 | `program.Runner`、`MotionExecutor`、`RunJournal` |
| 安全 | `safety`、`preview_safety`、`obj_hits`、`_safe_cache` |
| 预览/目标 | `preview_q`、`_motion_targets`、`_target_evidence`、`_arrival_evidence` |
| 真机 | `real`（`ServoCapableRealLink`）、`real_jog_follow`、`real_jog_*` 门禁 |
| 跟随 | `hand_follow`（仿真）、`hand_track`、`_hand_*` 会话/输入身份 |
| 业务 | `robot_play`、`motion_session`、仿真 IO、吸盘、工具动作 |
| 遥测 | `playback`、`trend`、`telemetry_seq`、`blackbox` |
| 生命周期 | `session_id`、`_task`、`_stop_task`、`_challenge`、`_armed` |

### 2.1 生命周期与主循环

| 方法 | 位置 | 说明 |
| --- | --- | --- |
| `start()` | [L290](../../backend/core/world.py#L290) | 启动 50Hz 控制循环；按设置决定是否自动连真机 |
| `stop()` | [L317](../../backend/core/world.py#L317) | 先走停止事务，再取消后台任务、关真机链路 |
| `_loop()` | [L381](../../backend/core/world.py#L381) | tick、保存仿真姿态、按 20/30/60Hz 广播快照 |
| `_tick()` | [L505](../../backend/core/world.py#L505) | 推进仿真/真机状态、安全、执行器、跟随、遥测 |
| `snapshot()` | [L4059](../../backend/core/world.py#L4059) | 输出快照；快字段每帧，遥测 1Hz |
| `build_telemetry()` | [L4143](../../backend/core/world.py#L4143) | 偏差、体检、回放、健康、趋势 |
| `health()` | [L4194](../../backend/core/world.py#L4194) | 每轴温度/电流/限位/状态摘要 |

### 2.2 运动与目标

| 方法 | 位置 | 说明 |
| --- | --- | --- |
| `precheck()` | [L940](../../backend/core/world.py#L940) | 目标限位、模式、安全前置检查 |
| `_execute_move()` | [L980](../../backend/core/world.py#L980) | 仿真/真机分支的移动执行 |
| `_execute_movel()` | [L1034](../../backend/core/world.py#L1034) | MOVEL 曲线预检与执行 |
| `_real_movejto()` | [L1069](../../backend/core/world.py#L1069) | 真机绝对点（需资格/记录） |
| `_real_rel_step()` | [L1182](../../backend/core/world.py#L1182) | 真机 MoveRelJ 逐轴逼近 |
| `register_motion_target()` | [L714](../../backend/core/world.py#L714) | 登记带 `plan_id` 的目标 |
| `execute_motion_target()` | [L745](../../backend/core/world.py#L745) | 通过目标执行，绑定上下文 |
| `inspect_plan()` / `execute_plan()` | [L2496](../../backend/core/world.py#L2496) / [L2513](../../backend/core/world.py#L2513) | 外部"先检查后执行"入口 |
| `plan_context()` | [L2478](../../backend/core/world.py#L2478) | 计划上下文摘要，用于失效判定 |

### 2.3 安全与停止

| 方法 | 位置 | 说明 |
| --- | --- | --- |
| `preview_safety()` | [L766](../../backend/core/world.py#L766) | 预览残影的安全评估，不运动 |
| `_object_hits()` | [L781](../../backend/core/world.py#L781) | 物体命中判定 |
| `robot_overlap()` | [L797](../../backend/core/world.py#L797) | 落位前防重叠 |
| `_on_alarm()` | [L813](../../backend/core/world.py#L813) | 告警/暂停/退离提示 |
| `_recede_allowed()` | [L911](../../backend/core/world.py#L911) | 仿真"沿脱离方向退出"放行 |
| `stop_execution()` | [L2541](../../backend/core/world.py#L2541) | 停止事务：撤执行、发停止、等停稳 |
| `estop()` | [L2528](../../backend/core/world.py#L2528) | 急停入口 |

### 2.4 电源/就绪/模式/真机

| 方法 | 位置 | 说明 |
| --- | --- | --- |
| `ready()` | [L1624](../../backend/core/world.py#L1624) | 一键就绪泵：看 FSM、逐步下发、等到位 |
| `power()` | [L1521](../../backend/core/world.py#L1521) | 上电/初始化/使能/下电/清错/停止 |
| `_wait_power_state()` | [L1464](../../backend/core/world.py#L1464) | 等控制器 FSM/使能回读 |
| `set_speed()` | [L1856](../../backend/core/world.py#L1856) | 仿真/真机速度分开保存，真机下发 override |
| `connect_real()` | [L2299](../../backend/core/world.py#L2299) | 连接控制器 |
| `disconnect_real()` | [L2315](../../backend/core/world.py#L2315) | 断开并清理 |
| `move_joints()` | [L2349](../../backend/core/world.py#L2349) | 关节移动入口（含 allowed_axes） |
| `jog()` | [L2324](../../backend/core/world.py#L2324) | 点动开/关 |
| `write_end_do()` | [L2416](../../backend/core/world.py#L2416) | 末端 DO 写入 |
| `io_probe()` | [L2935](../../backend/core/world.py#L2935) | IO 只读探测 |
| `diag_bundle()` | [L2992](../../backend/core/world.py#L2992) | 黑匣子诊断包 |

### 2.5 工具/吸盘/程序/AI/跟随

| 方法 | 位置 | 说明 |
| --- | --- | --- |
| `select_tool()` / `clear_tool()` | [L2077](../../backend/core/world.py#L2077) / [L2108](../../backend/core/world.py#L2108) | 工具档案选择/清空 |
| `tool_action()` | [L2229](../../backend/core/world.py#L2229) | 工具动作（含左右吸盘互斥） |
| `_suction_action()` | [L2162](../../backend/core/world.py#L2162) | 吸盘/破真空状态机 |
| `set_sim_tool()` | [L231](../../backend/core/world.py#L231) | 仿真末端模型/TCP/包络切换 |
| `load_program()` | [L2402](../../backend/core/world.py#L2402) | 解析/编译程序 |
| `start_program()` / `pause_program()` / `resume_program()` | [L2718](../../backend/core/world.py#L2718) / [L2729](../../backend/core/world.py#L2729) / [L2745](../../backend/core/world.py#L2745) | 程序控制 |
| `resume_last()` | [L473](../../backend/core/world.py#L473) | 断点续跑（不自动执行） |
| `ai()` / `ai_run()` | [L3069](../../backend/core/world.py#L3069) / [L3133](../../backend/core/world.py#L3133) | AI 对话/白名单计划执行 |
| `hand_follow_action()` | [L3724](../../backend/core/world.py#L3724) | 仿真手掌跟随配置/启动/停止 |
| `real_hand_follow_action()` | [L3494](../../backend/core/world.py#L3494) | 真机 Servo 跟随 |
| `hand_track_action()` | [L3677](../../backend/core/world.py#L3677) | 末端相机视觉伺服；有范围盒时末端站到手掌后方并保持固定朝向，无盒时退回纯朝向调整；仅仿真 |

## 3. 运动学、轨迹、规划、安全

### 3.1 `RobotModel`（[kinematics.py](../../backend/core/kinematics.py#L170)）

从 `web/public/models/<variant>/robot.json` 读取关节/连杆/网格包围盒：

- `fk(q_deg)`（[L234](../../backend/core/kinematics.py#L234)）：前向运动学，返回每个 link 的 4×4 矩阵；带单姿态缓存。
- `flange_pose(q)`（[L263](../../backend/core/kinematics.py#L263)）：法兰 + TCP 偏移后的 `xyz_mm/rpy_deg`。
- `capsules(q, conservative=...)`（[L270](../../backend/core/kinematics.py#L270)）：每连杆胶囊包络；保守模式沿最长轴展开，用于碰撞。
- `moving_ground_clearances()`（[L348](../../backend/core/kinematics.py#L348)）：可动连杆离地间隙。
- `tool_plane_gaps()`（[L378](../../backend/core/kinematics.py#L378)）：末端工具到围栏各面的距离。
- `pose_vec()` / `jacobian()` / `ik()`（[L426](../../backend/core/kinematics.py#L426)/[L439](../../backend/core/kinematics.py#L439)/[L460](../../backend/core/kinematics.py#L460)）：位姿向量、数值雅可比、阻尼最小二乘 IK。
- `capsule_distance()`（[L607](../../backend/core/kinematics.py#L607)）：两胶囊最短距离。

约束：位置 mm、姿态角度；TCP 偏移只应用一次；`RobotModel` 不负责授权或运动。

### 3.2 `JointTrajectory` / `PreparedMotion`（[trajectory.py](../../backend/core/trajectory.py)）

- `JointTrajectory.build()`（[L103](../../backend/core/trajectory.py#L103)）：给定关节路点、`vmax`、速度百分比，生成时间参数化曲线；支持 `quintic` 与 `trapezoid` 两种速度形状。
- `at(elapsed)`（[L156](../../backend/core/trajectory.py#L156)）：按时间取关节角。
- `inspection_samples(spacing)`（[L160](../../backend/core/trajectory.py#L160)）：供预检按实际 C1 曲线采样，而不是只查弦线。
- `PreparedMotion.report()`（[L183](../../backend/core/trajectory.py#L183)）：预检报告（风险、时长、采样等）。

注意：它是**关节空间曲线**，不是控制器认证的笛卡尔插值；MOVEL 仍要按项目约定说明"近似直线"。

### 3.3 规划（[planning.py](../../backend/core/planning.py)）

| 函数 | 位置 | 作用 |
| --- | --- | --- |
| `digest(value)` | [L33](../../backend/core/planning.py#L33) | 规范化 JSON 摘要，用于计划上下文 |
| `waypoints()` | [L38](../../backend/core/planning.py#L38) | 按策略生成关节路点 |
| `inspect_path()` | [L69](../../backend/core/planning.py#L69) | 采样、限位、围栏/地面、物体碰撞、可退离判定、条件数检查 |
| `prepare_movel()` | [L204](../../backend/core/planning.py#L204) | 生成 MOVEL C1 曲线并预检每个结节 |
| `eigenvalues_symmetric()` | [L48](../../backend/core/planning.py#L48) | 条件数近似 |

`inspect_path()` 的关键实现细节：

- 仿真按 2° 采样，真机保持 1°；物体×连杆×采样点有求距预算，超预算时放宽角度采样而不是退回"只查末端"。
- 物体求距先用外接球下界剪枝，再算带符号间隙；负间隙才算穿透。
- 仿真起点已相交时，只放行"间距单调变大"的退离采样；真机不走这段豁免。
- 围栏/地面仍按末端工具口径；物体按全连杆口径。

### 3.4 安全评估（[safety.py](../../backend/core/safety.py)）

| 函数 | 位置 | 作用 |
| --- | --- | --- |
| `limit_ratios()` | [L21](../../backend/core/safety.py#L21) | 每轴占限位幅值比例 |
| `classify()` | [L30](../../backend/core/safety.py#L30) | 阈值分级 |
| `_fence_clearance()` | [L46](../../backend/core/safety.py#L46) | 胶囊表面到四面围栏的带符号距离 |
| `_ground_clearance()` | [L56](../../backend/core/safety.py#L56) | 胶囊到地面的间隙 |
| `tool_capsules()` | [L60](../../backend/core/safety.py#L60) | 围栏/地面只监测末端工具 |
| `object_capsules()` | [L66](../../backend/core/safety.py#L66) | 摆放物监测全部可动连杆，排除底座 |
| `_object_signed_gap()` | [L117](../../backend/core/safety.py#L117) | 胶囊-物体带符号间隙，支持球-球下界 |
| `contact_profile()` | [L158](../../backend/core/safety.py#L158) | 记录接触/间距快照 |
| `recede_verdict()` | [L196](../../backend/core/safety.py#L196) | 判断是否在沿脱离方向退出 |
| `evaluate()` | [L236](../../backend/core/safety.py#L236) | 汇总安全等级、碰撞、原因、胶囊 |

默认语义：90% 黄、95% 红、100% 告警；仿真关节接近限位只提示，真机/硬边界才拦截。

## 4. 程序系统

### 4.1 ElfinScript 行指令

[program.py](../../backend/core/program.py) 支持：

```text
# 注释
SPEED 12
MOVEJ P3
MOVEJ 10,20,30,40,50,60
MOVEL x,y,z,a,b,c
DOUT 0,1
WAIT 0.5
LOOP 3 ... END
PAUSE
TOOL OPEN|CLOSE|VACUUM_ON|VACUUM_OFF
REQUIRE_TOOL <id>
```

- `ProgramError`（[L40](../../backend/core/program.py#L40)）：带行号、原文、原因；
- `parse()`（[L175](../../backend/core/program.py#L175)）：逐行解析，限制步数/循环展开；
- `compile_steps()`（[L243](../../backend/core/program.py#L243)）：把指令编译成 Runner 可消费的步骤；
- `Runner`（[L355](../../backend/core/program.py#L355)）：`idle/running/waiting/paused/finished/aborted/error`；由 `World.tick` 推进，不自己起线程；支持断点/单步/变量展示（用于旧调试接口，前端已不调用）。

### 4.2 受限 Python 子集

[python_program.py](../../backend/core/python_program.py)：

- `normalized()`（[L29](../../backend/core/python_program.py#L29)）：中文关键字映射（如果/循环/定义……）；
- `validate()`（[L37](../../backend/core/python_program.py#L37)）：AST 白名单，禁止 import、eval/exec、文件/网络、任意属性；限制节点数、名称、函数形式；
- `Interpreter`（[L85](../../backend/core/python_program.py#L85)）：协作式解释器，不执行任意代码；限制数据成本、深度、原生工作量；
- 内置命令仍落到 `SPEED/MOVEJ/MOVEL/WAIT/DOUT/WAIT_DI/TOOL/REQUIRE_TOOL/PAUSE`。

### 4.3 程序存储

[program_store.py](../../backend/core/program_store.py#L14)：`programs.json` 的读写、名称校验、列表/读取/保存/删除；程序源码是用户数据，不写进日志。

## 5. 取放与任务执行

### 5.1 `RobotPlay`（[robot_play_routes.py](../../backend/robot_play_routes.py#L39)）

职责：**仿真取放/分拣/棋局的唯一执行器**，复用 `program.Runner` 与 `MotionExecutor`，不拥有真机通道。

| 方法 | 位置 | 说明 |
| --- | --- | --- |
| `vision_snapshot()` | [L49](../../backend/robot_play_routes.py#L49) | 只读快照；不打开/扫描相机 |
| `context(controlled)` | [L72](../../backend/robot_play_routes.py#L72) | 计划上下文：会话/模式/工具/场景/接触 ID |
| `generate(payload)` | [L133](../../backend/robot_play_routes.py#L133) | 预检 + 生成取放计划；可取消 |
| `run(payload)` | [L300](../../backend/robot_play_routes.py#L300) | 只运行已预检且上下文未变的计划 |
| `guard()` | [L322](../../backend/robot_play_routes.py#L322) | 启动前检查空闲/持件/停止 |
| `grip(kind, oid)` | [L344](../../backend/robot_play_routes.py#L344) | 吸附/释放仿真工件 |
| `tick()` | [L393](../../backend/robot_play_routes.py#L393) | 跟随 Runner 推进、检查持件/接触 |
| `stop()` | [L422](../../backend/robot_play_routes.py#L422) | 取消预检/执行，保留实际位置 |
| `release_manual()` | [L462](../../backend/robot_play_routes.py#L462) | 手动释放持件 |

`carried_clearance()`（[L22](../../backend/robot_play_routes.py#L22)）在持件时检查工件穿地、与场景物体/非工具连杆相交。

### 5.2 `PlayService`（[play_routes.py](../../backend/play_routes.py#L58)）

只做**场景道具事务**：布置、重置、落子、分组；每个事务在对象锁内深拷贝候选、落盘、再发布内存。它不调用机器人运动接口。

### 5.3 应用任务

| 服务 | 文件 | 主要方法 | 说明 |
| --- | --- | --- | --- |
| `Drawing` | [application/drawing.py](../../backend/application/drawing.py#L21) | `preview` [L118](../../backend/application/drawing.py#L118)、`run` [L387](../../backend/application/drawing.py#L387)、`cancel` [L430](../../backend/application/drawing.py#L430)、`pause` [L444](../../backend/application/drawing.py#L444)、`resume` [L450](../../backend/application/drawing.py#L450) | 几何→IK→曲线→预检→执行；计划 TTL/起点/工具/场景绑定 |
| `AssemblyTasks` | [application/assembly_tasks.py](../../backend/application/assembly_tasks.py#L46) | `setup` [L53](../../backend/application/assembly_tasks.py#L53)、`preview` [L78](../../backend/application/assembly_tasks.py#L78)、`inspect` [L107](../../backend/application/assembly_tasks.py#L107)、`run` [L127](../../backend/application/assembly_tasks.py#L127) | 装盒/拼摆，有限颜色/格位/容量 |
| `HandlingChallenge` | [application/handling_challenge.py](../../backend/application/handling_challenge.py#L13) | `setup` [L23](../../backend/application/handling_challenge.py#L23)、`inspect` [L69](../../backend/application/handling_challenge.py#L69)、`run` [L92](../../backend/application/handling_challenge.py#L92)、`event` [L108](../../backend/application/handling_challenge.py#L108) | 关卡布置 + 实际位置计分 |
| `VisionTasks` | [application/vision_tasks.py](../../backend/application/vision_tasks.py#L12) | 找物/计数/尺寸定位/标定校验 | 只读视觉快照，不运动 |
| `RunJournal` | [application/run_journal.py](../../backend/application/run_journal.py#L43) | 观察 Runner 事件，写有界运行记录 | 只观察，不重放命令 |
| `DataBackup` | [application/data_backup.py](../../backend/application/data_backup.py#L197) | 导出/预检/恢复副本/激活计划 | 恢复只创建新目录，不改当前 World |
| `RemoteCamera` | [application/remote_camera.py](../../backend/application/remote_camera.py#L19) | 配对、加入、SDP、心跳、撤销 | 单向 WebRTC，只读工件/手掌结果 |

## 6. 视觉与相机

### 6.1 `CameraManager`（[cameras.py](../../backend/core/cameras.py#L15)）

- `scan()`（[L221](../../backend/core/cameras.py#L221)）：枚举 RealSense/USB 相机，合并物理设备身份别名，扫描结束释放句柄。
- `open()`（[L277](../../backend/core/cameras.py#L277)）：按流配置打开采集；与 `VisionWorkspace` 共用锁，避免两窗口双开。
- `image()` / `stream_frame()`（[L561](../../backend/core/cameras.py#L561) / [L568](../../backend/core/cameras.py#L568)）：返回最新 JPEG 与序号/时间戳；会话不匹配直接失效。
- `raw_frame_copy()`（[L583](../../backend/core/cameras.py#L583)）：给检测管线复制原始帧，避免主循环做 720p 拷贝。
- `observation_stamp()` / `observation_age_ms()`（[L597](../../backend/core/cameras.py#L597) / [L610](../../backend/core/cameras.py#L610)）：服务端采集时间与帧龄。
- `pointcloud()` / `measure()`（[L617](../../backend/core/cameras.py#L617) / [L628](../../backend/core/cameras.py#L628)）：RealSense 点云/测距。
- `snapshot()`（[L642](../../backend/core/cameras.py#L642)）：设备、活动会话、SDK 可用性、错误。

**读取状态永远不会打开设备**；采集只在显式 `open` 后发生。

### 6.2 `VisionWorkspace`（[vision_workspace.py](../../backend/core/vision_workspace.py#L60)）

- `configure()`（[L98](../../backend/core/vision_workspace.py#L98)）：校验来源（camera/image/virtual/remote）、颜色、ROI、虚拟相机参数。
- `snapshot()`（[L116](../../backend/core/vision_workspace.py#L116)）：配置、标定、最新识别、跟踪、错误、generation。
- `process()`（[L196](../../backend/core/vision_workspace.py#L196)）：取最新帧→分析→跟踪→发布。
- `analyze()`（[L237](../../backend/core/vision_workspace.py#L237)）：HSV 颜色/轮廓、中心、角点、像素统计。
- `track()`（[L329](../../backend/core/vision_workspace.py#L329)）：临时跟踪 ID，结果带 `stale/fresh`。
- `calibrate()`（[L351](../../backend/core/vision_workspace.py#L351)）：六点平面标定，限制误差。
- `upload()` / `virtual_frame()`（[L391](../../backend/core/vision_workspace.py#L391) / [L403](../../backend/core/vision_workspace.py#L403)）：图片/虚拟相机帧。
- `start()` / `close()`（[L419](../../backend/core/vision_workspace.py#L419) / [L429](../../backend/core/vision_workspace.py#L429)）：采集/处理线程生命周期。

标定只在当前进程/相机会话有效，重启后要求重新确认；旧标定不静默恢复为运动坐标。

### 6.3 `VisionPipeline`（[application/vision_pipeline.py](../../backend/application/vision_pipeline.py#L11)）

- 一个最新帧槽 + 工作线程；`TTL=0.3s`；
- `prewarm()`（[L52](../../backend/application/vision_pipeline.py#L52)）：启动后后台加载模型；
- 失败/关闭会回滚检测状态；检测帧带来源会话、序号、采集时间；
- 与 `ProcessHandDetector` IPC：stdin/stdout JSON 行，独立进程，不共享 OpenCV/MediaPipe。

### 6.4 `ProcessHandDetector`（[infrastructure/vision/process_detector.py](../../backend/infrastructure/vision/process_detector.py#L15)）

优先级：

1. `ELFIN_VISION_WORKER`（打包态 `ElfinVision.exe`）；
2. `ELFIN_VISION_PYTHON`（`.venv-vision`）；
3. 源码树自带 `.venv-vision`；
4. 当前解释器。

模型默认 `web/public/vision/hand_landmarker.task`，打包态取 `web/dist/vision/`。

## 7. 真机链路

### 7.1 协议与连接

[protocol.py](../../backend/core/protocol.py)：`encode()`、`split_frames()`、`parse_reply()`、`decode_state()`；数据表字段与官方文档对齐。

`RealLink`（[robot_real.py](../../backend/core/robot_real.py#L125)）：

- 命令通道与状态通道分离；
- `command_session` 只在命令连接变化时递增；状态流 `epoch` 单独递增；
- `Reply`（[L109](../../backend/core/robot_real.py#L109)）：ACK/拒绝/超时/提示；
- 状态快照字段全部先置 `None`，未收到就保持未知；
- 身份查询只在未使能、静止、无排队命令时做，失败不循环重试。

`ServoCapableRealLink`（[real_follow_link.py](../../backend/core/real_follow_link.py#L67)）在 `RealLink` 上增加 Servo 专用通道与 `_LinkTransport`（[L46](../../backend/core/real_follow_link.py#L46)）。

### 7.2 Servo 与跟随

| 组件 | 位置 | 说明 |
| --- | --- | --- |
| `ServoProfile` | [real_servo.py](../../backend/core/real_servo.py#L31) | 现场调试参数：周期、前瞻、回执期限、反馈时效、跟踪误差、限位/速度/加速度、证据 |
| `RealServoSession` | [real_servo.py](../../backend/core/real_servo.py#L74) | 固定周期生产者；任何故障锁存；停止是尝试不是证明 |
| `ExclusiveServoTransport` | [real_servo_transport.py](../../backend/core/real_servo_transport.py#L58) | 单仲裁器、有界 FIFO、超时/取消后不重放、未知结果隔离 |
| `RealPalmFollowController` | [real_follow_runtime.py](../../backend/core/real_follow_runtime.py#L25) | World 持有的真机手掌跟随控制器；只用已确认的调试记录/工具 |
| `RealPalmFollowHost` | [real_follow_host.py](../../backend/core/real_follow_host.py#L28) | 跟随生命周期；依赖限定执行器、反馈、上下文、路径验证 |
| `RealFollowStartup` | [real_follow_startup.py](../../backend/core/real_follow_startup.py#L17) | 先到中心、J6 归零的启动流程 |
| `FiniteCenterExecutor` | [real_follow_finite.py](../../backend/core/real_follow_finite.py#L16) | 有限中心运动 |
| `RealPalmTargetPipeline` | [real_palm_pipeline.py](../../backend/core/real_palm_pipeline.py#L23) | 目标限幅/最新值/时效 |
| `RealJogFollow` | [real_jog_follow.py](../../backend/core/real_jog_follow.py#L29) | 真机点动式跟随状态机 |
| 资格记录 | `real_follow_qualification.py`、`real_movel_qualification.py` | 现场调试记录加载/匹配；没有匹配记录就拒绝 |

**真机当前设计只把 MoveRelJ 逐轴逼近当作已实测路径**；MoveJTo/MOVEL/Servo 需要各自记录与门禁。代码里存在这些能力不等于当前设备已通过。

### 7.3 真机安全门禁

- `motion_granted = FSM 33 && enabled`（[controls.py](../../backend/core/controls.py#L33)）。
- 停止绑定原命令会话，不绑定状态 `epoch`（[robot_real.py](../../backend/core/robot_real.py#L135-L137)）。
- `stop_execution()` 要求收到"未使能 + 静止"或明确的停稳证据；超时保持闭锁。
- 连接断开、反馈过期、工具未确认、路径记录不匹配都会拒绝新动作。

## 8. 工具、物体、点位、场景、日志、遥测

| 模块 | 位置 | 说明 |
| --- | --- | --- |
| `ToolStore` | [tools.py](../../backend/core/tools.py#L43) | 工具档案、安装状态、身份来源分离、动作映射、IO 确认、修订历史 |
| `tool_library` | [tool_library.py](../../backend/core/tool_library.py#L9) | 只读本地工具目录；校验资产/包络/动画；不配置真机 |
| `sim_tool` | [sim_tool.py](../../backend/core/sim_tool.py#L15) | 仿真末端模型/TCP/包络归一化 |
| `ObjectStore` | [objects.py](../../backend/core/objects.py#L58) | 箱/球/圆柱、旋转缩放、重力/落地、碰撞代理、持久化、事务 |
| `PointStore` | [points.py](../../backend/core/points.py#L16) | 点位关节角/TCP/来源；固定 P 编号；只允许改名称 |
| `SceneStore` | [scenes.py](../../backend/core/scenes.py#L18) | 多现场文件，只保存仿真道具布局；名称/数量校验 |
| `SceneAssetStore` | [scene_assets.py](../../backend/core/scene_assets.py#L195) | 有界不可变 GLB 缓存；校验来源/许可/单位/结构；不导入贴图/外链 |
| `CalibrationStore` | [calibration.py](../../backend/core/calibration.py#L65) | 工位/TCP/IO 标定记录 |
| `EventLog` | [eventlog.py](../../backend/core/eventlog.py#L23) | 环形内存 + 追加 JSONL；增量查询、CSV 导出、清空 |
| `Playback` / `deviation` / `preflight` | [Playback](../../backend/core/telemetry.py#L28)、[deviation](../../backend/core/telemetry.py#L102)、[preflight](../../backend/core/telemetry.py#L133) | 回放环形缓冲、影子孪生偏差、就绪前体检 |
| `write_json` | [storage.py](../../backend/core/storage.py#L8) | 临时文件 + fsync + 原子替换 |
| `ServiceLease` | [service_lease.py](../../backend/core/service_lease.py#L6) | 操作系统文件锁，保证单控制进程 |

## 9. AI 计划

两层：

1. **本地规则**：[ai_intent.py](../../backend/core/ai_intent.py#L83) 的 `intent()` 识别速度/点位/DO/等待/工具/绘制等明确指令；`compile_plan()`（[L203](../../backend/core/ai_intent.py#L203)）编译成白名单动作。
2. **模型辅助**：[ai_plan.py](../../backend/core/ai_plan.py#L58) 的 `validate()` 把模型输出压回 `ALLOWED_OPS`；任何点位必须在点位表存在，任何越限/未消费字段整条拒绝。`make_plan()`（[L237](../../backend/core/ai_plan.py#L237)）函数默认 `allow_model=False`；当前 AI 面板发送时会显式携带 `allow_model=true`，是否真正外发仍取决于是否配置了模型/Key，且执行仍需人工确认。

AI 只能产出白名单计划，执行仍需人工确认；模型解释不产生执行许可。

## 10. 路由一览

路由都是薄层，实际逻辑在 `World`/应用服务。按前缀分组：

| 前缀 | 文件 | 代表能力 |
| --- | --- | --- |
| `/api` 根 | [main.py](../../backend/main.py) | state/settings/speed/logs/model/meta/capabilities/control/ready/power/free/jog/movej/preview/ik/fk/estop/io/real/points/program/ai/plans/blackbox/calibrations/ws |
| `/api/studio` | [task_routes.py](../../backend/task_routes.py) | 工艺录制只读兼容 |
| `/api/teaching` | [teaching_routes.py](../../backend/teaching_routes.py) | 批量示教 |
| `/api/workspace` | [workspace_routes.py](../../backend/workspace_routes.py#L417) | 配置档案、场景模板/阵列/对齐/增删/撤销重做 |
| `/api/play` | [play_routes.py](../../backend/play_routes.py#L183) | 场景玩法事务 |
| `/api/play/program` | [robot_play_routes.py](../../backend/robot_play_routes.py#L471) | 取放预检/运行/取消/释放 |
| `/api/drawing` | [drawing_routes.py](../../backend/drawing_routes.py) | 绘制预览/运行/取消/暂停/继续 |
| `/api/challenge/handling` | [handling_challenge_routes.py](../../backend/handling_challenge_routes.py) | 搬运闯关 |
| `/api/assembly` | [assembly_routes.py](../../backend/assembly_routes.py) | 装盒/拼摆 |
| `/api/vision/markers` | [vision_routes.py](../../backend/vision_routes.py#L233) | 只读视觉能力/检测 |
| `/api/vision/workspace` | [vision_workspace_routes.py](../../backend/vision_workspace_routes.py) | 视觉工作区状态/配置/标定/图像/虚拟帧 |
| `/api/vision/pipeline` | [vision_pipeline_routes.py](../../backend/vision_pipeline_routes.py) | 手掌检测管线配置/帧 |
| `/api/vision/remote` | [remote_camera_routes.py](../../backend/remote_camera_routes.py) | 远程相机配对/加入/心跳/撤销 |
| `/api/vision/tasks` | [routes/vision_tasks.py](../../backend/routes/vision_tasks.py) | 找物/计数 |
| `/api/cameras` | [main.py](../../backend/main.py#L489) | 扫描/开/关/JPEG/MJPEG/点云/测距 |
| `/api/scene-assets` | [scene_asset_routes.py](../../backend/scene_asset_routes.py) | GLB 检查/导入/模型下载 |
| `/api/runs` | [run_journal_routes.py](../../backend/run_journal_routes.py) | 运行记录列表/详情/导出 |
| `/api/backup` | [data_backup_routes.py](../../backend/data_backup_routes.py) | 导出/预检/恢复副本/激活计划 |
| `/api/motion` | [motion_session_routes.py](../../backend/motion_session_routes.py) | 单点/任务暂停/继续/取消 |
| `/api/program/debug` | [debug_routes.py](../../backend/debug_routes.py) | 旧调试接口仍挂载，前端已无调用方 |

## 11. 后端写作约定

- 路由里不创建 `World`、不打开设备、不起长期线程；需要资源时从 `app.state.resources` 或注入的服务拿。
- 新增长任务必须能被取消；取消后旧结果不能重新生效。
- 所有写入走 `write_json`/存储对象，不直接 `open(...,'w')` 覆盖用户数据。
- 控制器未给的字段保持 `None`；不要用 0/仿真值填充真机读数。
- 安全相关逻辑优先放在 `safety.py`/`planning.py`，由 `World` 调用；不要在 UI 或路由里另写一套阈值。
- 改动真机分支时，先确认 `HUYAN_NO_REAL`、`command_session`、停止绑定和资格记录四条链路都未被绕过。
