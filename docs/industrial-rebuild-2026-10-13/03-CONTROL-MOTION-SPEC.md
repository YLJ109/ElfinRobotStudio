# 03 机器人控制、运动与程序规范

## 1. 控制信息和命令的覆盖定义

“所有”定义为指定型号/固件的公开受支持接口，不能覆盖未公开厂家内部功能。建立完整资料目录并逐项列出适用性、接入状态和证据；不使用一个通用文本输入框代替完整控制系统。

能力分级：`documented` 文档存在 → `implemented` 接入 → `offline_verified` 契约/模拟验证 → `hardware_verified` 特定条件真机验证 → `qualified` 满足指定工艺验收。等级与“已安装/已启用”是独立维度。

每条指令包含：原名、语义、版本、参数/类型/单位/范围、前置 FSM、回执、错误、幂等性、可否重试、是否只读、完成观测、超时、停止关系、现场证据。每个字段包含：原键、类型、单位、数据源、新鲜度、缺省策略与版本。

| 能力域 | 目录内容 | 当前/目标说明 |
|---|---|---|
| 身份 | 型号、固件、库版本、控制器状态 | 只读优先匹配实际设备 |
| 生命周期 | 连接、上电、主站、使能、复位、待机 | 已有链路基础，保持已验证 FSM 时序 |
| 停止与暂停 | GrpStop/Interrupt/Continue、程序停止 | 明确命令层与实际停稳，不泛化安全等级 |
| 实际运动状态 | q/qdot、TCP、moving/InPos、轨迹 ID | 字段缺失不能用于完成判据 |
| 点动 | joint/cartesian short/long/keepalive | 仅启用型号/固件实际支持的动作 |
| 目标运动 | 相对关节、MOVEJ、MOVEL、圆弧 | 各自参数和路径语义，未验证不自动替换 |
| 轨迹 | 控制器插补、轨迹/队列/融合 | 需要厂家接口及现场验证 |
| 坐标与 TCP | 工件坐标、工具名、TCP读写 | 读回及 revision，一处事实源 |
| 负载 | 质量、重心、惯量、负载识别 | 档案与实机回读分开 |
| IO | 控制柜/末端 DI/DO/AI/AO | 位号、极性、回执和反馈不混淆 |
| 程序 | RunFunc、Start/Stop/Pause/Continue/SwitchScript | 先控制已有程序，上传接口另行资格确认 |
| 诊断 | 错误、温度、电流、力、通信、日志 | 无传感器或错误表缺失明确未知 |
| 自由拖动 | Basic/Advanced、抱闸/力控模式 | 能力与现场机械风险单独验证 |
| 选配扩展 | 力控、视觉、快换、总线 | 有硬件/许可/驱动证据才开放 |

本地 `tools/proto.txt` 的 V1.0.19.1 协议 §4.3、页 30–37 给出程序生命周期；必须查原 PDF 核对乱码抽取内容、参数和本机固件。`SwitchScript` 选择 `.JSON` 程序名不意味着有“上传任意 JSON”能力。[官方软件下载页](https://www.huayan-robotics.com/service/download/software)

## 2. 身份与参数档案

连接后读取型号/固件/功能，不触发运动。模型匹配记录：控制器返回原始型号、厂家对应关系、URDF hash、关节极限、速度/加速度/jerk、法兰参考点、TCP、负载、版本来源。

来源优先级不是“官网最新一定覆盖现场”，而是经确认适用的该台设备固件/机械资料与现场标定。冲突进入待确认，保留双方原始证据。`E05-1195_Pro` 不自动映射为当前官网 E05-Pro 或 E05L-Pro。

厂家最高速度不是推荐作业速度。额定负载、TCP 距离、重心、惯量、工位环境与工具的限制要共同参与运动参数校验。

## 3. 状态契约

```text
StateSample:
  robot_id, session_id, connection_epoch, seq
  source_timestamp?                 厂家有可靠时间才设置
  received_monotonic, received_utc
  quality = valid | incomplete | stale | disconnected | invalid
  q_rad?, velocity_rad_s?, pose?, fsm?, enabled?, moving?, in_position?
  field_quality{key: source, present, age, unit}
```

内部统一 SI 单位（rad/m/s/kg），厂家适配层与 UI 明确转换；现有度/mm 接口经过兼容桥，不半套改制。所有 float 必须 finite；长度、范围、枚举、字符串和资源引用校验。

使用单调时钟判断超时与新鲜度，UTC 用于审计展示。缺少控制器采样时间时只能量测接收后延迟，不能声称端到端采样时延。

状态至少区分：API在线、数据流在线、命令通道在线、设备就绪、运动许可、工具可用。一个绿色“连接”灯不能表达全部状态。

## 4. 控制状态机

```text
offline → connecting → identifying → idle_not_ready → ready → executing
                                    ↘ fault/locked ←───────┘
executing → stopping → stopped
executing → interrupted → recovery_required
executing → result_unknown → recovery_required
任一有效状态 → 数据过期/断链 → locked
```

就绪链兼容现有历史实测：FSM 7 → Electrify → 14 → StartMaster → 24 → GrpEnable → 33，中间过渡值依实际控制器状态等待。不能将 FSM 数字直接作为所有固件统一状态；档案描述映射。

切换仿真/真机、工具、工位、控制权前撤销活动执行；重连建立新 epoch，禁止恢复旧许可或补发旧运动。仿真状态机支持无真机独立执行，仍保留模拟错误与停止语义。

## 5. 命令与执行生命周期

```text
draft → validated → preflighted → queued → sending → accepted
                                             ↘ rejected
accepted → observed_running → settling → reached → completed
sending/accepted → result_unknown
活动阶段 → cancel_requested → controller_stop_requested → stop_observed
```

请求记录 `request_id / execution_id / step_id / command_id / connection_epoch / execution_epoch`。accepted 仅代表回执接受，不表示运动开始或到位。完成必须使用同代次、新鲜状态和指定误差/停稳窗口。

唯一执行协调器仲裁程序、点位、手动点动、AI、原生程序；默认忙时拒绝新运动，排队/替换是显式策略。控制器原生程序运行也占用写入控制权，不能同时在线发另一段 MOVEJ。

服务端 idempotency key 防止浏览器重复提交创建第二任务，但不能据此声称厂家报文 exactly-once。指令已发而回执丢失时标记未知；只有证明未发出才允许重发非幂等动作。

到位条件要分别定义关节误差、TCP误差、速度/静止、InPos 和停稳窗口。软件数值求解精度、测量噪声、控制器到位容差和工艺定位精度不得共用一个阈值。

## 6. 停止与通信失效

停止顺序：撤销执行 epoch → 清除未发送项 → 取消应用任务 → 请求支持的控制器停止 → 等待新鲜状态确认 → 记录停止请求/回执/观察结果。即使应用取消成功，也不能宣称机器人已停止。

10003 为串行请求/回执链。停止可优先于未发送队列项，但不能假定可以插入正在等待回执的帧；超时后若专用停止端口或 out-of-band 机制未经厂家确认，不偷偷改端口。命令链不通时显示“停止状态未知”，硬件安全回路承担现场保护。

点动 keepalive、pointerup、pointercancel、tab隐藏、WS断链与超时各有停止路径；失联时不能依赖浏览器发出最后一次停止。网关 lease/watchdog 需验证厂家长期点动行为和独立停止机制。

数据过期阈值按实际帧周期/抖动设定并现场验证，不将一个通用 500 ms 宣称安全响应时间。断线不自动下电或松夹爪；这些动作可能导致掉件，需工艺定义。

## 7. 坐标与姿态

维护 SE(3) 坐标树：base → flange → mount/tool → TCP；workpiece/world 相对 base 标定。FK 计算法兰与 TCP分别返回；UI清楚标来源与坐标。

内部姿态用旋转矩阵/单位四元数。ABC 作为厂家/显示表达，明确固定轴顺序、正方向与角度单位；SLERP 或旋转向量用于插值，不逐轴线性插欧拉角。显示处理 ±180° 跨越和分支，底层不以显示取整参与计算。

q 连续分支保留实际转数与关节范围，不能统一 wrap 到 ±180° 造成虚假最短路径或超限。工具坐标增量必须通过所定义的 TCP 当前/目标姿态变换；帧选择切换不改变同一物理目标。

## 8. 笛卡尔联动与 IK

必须澄清联动的对象：

- 在基座坐标只改 X，理想目标的 Y/Z/姿态可保持，多个关节必须协同变化。
- 在工具坐标沿 X，基座 X/Y/Z 通常同时改变；工具旋转也可能改变 ABC 多个显示项。
- 部分可达时，达成姿态的多项值应由 FK(q) 更新，不能人为让所有轴都变化或悄悄松开锁定姿态。

求解流程：新目标版本 → 上一个有效 q 初值 → 几何雅可比 → 阻尼/线搜索 → 关节限位/分支/残差 → 工具和场景校验 → 返回目标/达成状态。有限重试与截止时间；工作池可取消过期计算。

代价函数包含位置/姿态权重、离当前分支距离、限位余量、奇异性；权重与单位归一化。雅可比条件数必须记录长度尺度，否则混合 mm/rad 的裸条件数不具可比性。

约束允许固定姿态、仅位置、指定轴自由，但必须由用户/工艺显式选择。普通滑块失败不能自动放开姿态约束来“看起来动了”。边界退回只用于预览，显示 fraction/requested/achieved；完整运动执行需完整可行计划。

前端最多一条活动 IK 请求、最新目标合并，迟到返回丢弃。后端独立校验 revision，防止其他客户端或运行线程改变环境。

## 9. MOVEJ / MOVEL / MOVEC

| 类型 | 路径定义 | 限制与验证 |
|---|---|---|
| MOVEJ | 同步关节路径 | TCP通常不是直线；关节速/加/jerk、碰撞 |
| MOVEL | TCP 直线 + 明确姿态插值 | 完整可达、连续IK分支、关节限位、奇异、工具 |
| MOVEC | 圆弧定义与方向 | 点共线/半径/姿态、路径覆盖和能力 |
| Jog joint | 单/多关节受控增量/速度 | 按住、超时、停止语义 |
| Jog Cartesian | 指定 frame 的 TCP 增量/速度 | 连续IK或厂家原生接口，不退化冒充直线 |

当前笛卡尔目标通过 IK 得到 q 再执行 MOVEJ。目标 UI 必须标明“关节移动到 TCP 目标”。没有经验证的 MOVEL 能力时，不能拆很多 MOVEJ 就宣称真机直线连续轨迹。

真机优先采用经资格确认的控制器原生 MOVEJ/MOVEL；现有 MoveRelJ 兼容逐轴分段保留为受限通道，其停顿和真实路径明确披露。升级平滑真机运动要验证 `MoveJTo/MoveLTo` 或轨迹接口的语义、回执和完成，不能只改3D插值。

速度/加速度/jerk统一进时间参数化，考虑负载。可评估 Ruckig 或控制器时间参数化；集成前验证许可、平台、范围与误差。[MoveIt 时间参数化文档](https://moveit.picknik.ai/main/doc/examples/time_parameterization/time_parameterization_tutorial.html)

连续融合仅用于允许的路径节点；精确到位、抓取、IO等待、工艺检查前必须停稳或满足已定义同步条件。禁止为了丝滑把必要 WAIT 删除。

## 10. 轨迹预检

输入冻结：起点、机器人型号/固件/模型、运动类型、工具/TCP/负载、坐标、工位、限速、程序与能力 revision。

输出：有效性、关节限位余量、速度/加速度/jerk峰值、TCP路径、奇异区域、碰撞/最小间隙、预计节拍、不可执行步与原因、采样密度及遗漏风险。

起点漂移、工具切换、场景变化、参数变化、连接 epoch 变化使计划失效。规划过程中使用快照，不共享可变 objects。

碰撞层：粗包络快速筛选 → mesh/BVH或convex精检 → 工具/工件扫掠体与连续检测。离散采样可漏掉中间碰撞，必须在报告标质量与采样间距；工具线缆、柔性管线等以独立保守包络建模。

通过预检代表计算模型未发现指定风险，不代表物理安全保证。未知工具、未验证坐标或缺失关键安全数据不能默认绿色通过。

## 11. 仿真等级

| 等级 | 内容 | 可声明 |
|---|---|---|
| S0 几何 | 模型、法兰、工具、坐标 | 外观/空间布局一致 |
| S1 运动学 | FK/IK、关节限制、路径类型 | 指定模型运动学一致 |
| S2 控制语义 | 执行生命周期、IO/工具、停止、错误 | 相同程序语义与能力约束 |
| S3 时序 | 加速/jerk、命令/反馈延迟、工艺时间 | 已标定工况下时序近似 |
| S4 动力学 | 质量/惯量/摩擦/负载/接触/气路 | 独立测试支持的动态一致性 |

每次验证注明实际等级。当前五次时间缩放平滑运动不等于厂家插补一致。先实现 S0–S2，再现场辨识 S3；S4 为独立工作包，不虚构无硬件数据的精确动力学。

确定性仿真固定 tick、seed和输入日志；视觉插值与物理/控制时间分开。GUI卡顿不改变仿真结果，速度倍增只改变仿真时间。回放和仿真永远不能获得真机写入句柄。

## 12. 程序系统

管线：source → tokenize → AST → typed IR → dependency resolve → capability check → preflight → frozen executable → step events。编辑器、图形编排、AI输出都进入同一 IR。

IR节点：MoveJ/MoveL/MoveC、Speed、Wait、WaitDI、SetOutput、ToolAction、Condition、Loop（有界）、CallSubprogram、Checkpoint、Assert。输入/输出、单位、循环限额、总耗时预算、错误分支明确。

每步等待其完成事件推进；速度等待回读、DO等待回执或实际条件、DI等待去抖/新鲜度、运动等待到位。程序断点不能在非幂等工具动作中间随便重放。

恢复记录：已完成步骤、正在发送/执行状态、实际 q、工具、抓取负载、IO、工件、版本和连接代次。只有工艺安全恢复点可续跑；环境无法确认就要求重新示教/复位流程。

## 13. 控制器原生程序

三种后端分别标记：

1. 应用在线 IR 执行：工作站逐步发动作，已有基础。
2. 控制器现有程序：核对固件后适配 RunFunc/Script 生命周期，保持控制权与状态观察。
3. 程序部署：只有厂家提供上传/存储接口、格式规范、权限、校验和运行约束后实施。不能凭公开的 ScriptStart 推断上传能力。

原生部署流程：离线编译 → 依赖检查 → 格式/版本验证 → 上传暂存 → 控制器回读/摘要校验 → 选择激活 → 预检 → 受控启动。若接口无法保证原子部署，使用厂家支持的命名/切换流程，并明确中断后恢复办法。

禁止靠 SSH 写未公开目录或模拟示教器 UI 冒充稳定上传 API。官方 ROS/ROS2 功能需要兼容型号、驱动方式、固件与 Linux 环境验证；官方 ROS2 仓库当前标为 beta，是候选适配器而非默认替换。[华沿 ROS2 仓库](https://github.com/huayan-robotics/elfin_robot_ros2)

## 14. 拟议 API

| 端点组 | 关键行为 |
|---|---|
| `/api/v2/robots/{id}/capabilities` | 字段/指令适用性与证据等级 |
| `/api/v2/robots/{id}/state` | 带质量、epoch和序号快照 |
| `/api/v2/control/lease` | 服务端控制权，单设备单写入 |
| `/api/v2/plans` | 冻结目标/上下文并返回预检结果 |
| `/api/v2/executions` | 凭计划与幂等键提交，返回执行记录 |
| `/api/v2/executions/{id}/stop` | 停止请求与观察分开 |
| `/api/v2/tools`、`/mounted-tool` | 工具库/安装状态/切换事务 |
| `/api/v2/programs`、`/compile` | 程序版本、IR诊断、依赖 |
| `/api/v2/controller-programs` | 已有程序管理与资格状态 |
| `/api/v2/calibrations` | 样本、拟合、独立验证、激活 |
| `/api/v2/diagnostics` | 脱敏证据包、回放与故障注入 |
| `/api/v2/events`、`/ws/v2` | 可靠执行事件 + 可丢旧状态订阅 |

所有接口是目标设计，当前服务仍使用旧端点。错误统一 code/message/details/request_id/retryable，HTTP状态与领域状态一致。

## 15. 验证重点

控制契约测试覆盖分包/粘包/乱码/超长帧/错命令回执/迟到回执/半断线/取消/多次停止；状态机模型测试覆盖跨所有活动阶段的失效。

运动测试覆盖随机可达姿态FK→IK→FK、工具偏置、多个初值、奇异、限位、多圈角、±180°姿态、坐标切换、完整/部分目标、路径连续、速加jerk、工具/载荷碰撞。

HIL专门验证MoveJTo/MoveLTo、原生程序、IO、工具、停止、负载与恢复；仅在相应硬件条件下记录 hardware_verified。
