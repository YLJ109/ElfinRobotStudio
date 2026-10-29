# Elfin 仿真/真机运动契约审查（初步证据及修复跟进）

审查日期：2026-10-26。范围：只读核对现有协议、运动规划、运动学、随动链路；只新增本报告和 `tools/check_motion_contract.py`。未连接硬件、未启动服务、未改现有源码。掌心实现冻结。本轮按“复现关键差异、追踪实际指令、保留未验证边界”的代码审查方法执行。

## 结论先行

最新增量：World已接入 `prepare_movel` 和 `execute_prepared_sim`，逐采样点 move_j/停等循环已经移除；预检与执行共享同一条不可变C1关节曲线，工具、场景、限位、工位/模式/连接变化会拒绝或中止。22项轨迹测试及当前17项契约审计通过。它仍是笛卡尔IK采样点间的关节近似，不保证严格直线、TCP速度或控制器加速度/jerk；以下关于“逐段停等”的旧描述仅保留追溯，不代表当前仿真实现。

**当前不能宣称仿真运动轨迹与 Elfin 真机一致，而且默认 MOVEJ 存在已复现的明确路径差异。**

当前跟进状态：MC02已经通过发包前拒绝和禁用UI阻止未资格路径，不是“心跳已验证”；MC03已接入SO(3)插值并补齐角度采样，主代理进一步修复了本审查发现的近π轴方向符号问题；保留原独立四元数参考与误差阈值，现有40个姿态探针全部匹配。姿态部分在本轮覆盖用例内通过，逐段停稳和控制器轨迹一致性仍未认证。MC01未改变真机默认值，未发送或接入新WayPoint指令。

同一个关节目标只保证目标数据可相同，不保证经过的位置、速度/加速度、过渡半径、IK 分支、控制器插补、跟踪误差或停止响应相同。官方 URDF 几何也不是控制器的标定运动模型或轨迹发生器。

## 官方依据及版本边界

1. [华沿官方 SDK_sample 仓库](https://github.com/huayan-robotics/SDK_sample)：README 指向 C++ 与 Java SDK 示例。
2. [官方 Sample_Move.cpp](https://raw.githubusercontent.com/huayan-robotics/SDK_sample/main/CppLinux_SDK/src/Sample_Move.cpp)：`HRIF_MoveJ` / `HRIF_MoveL` 使用 TCP、UCS、速度、加速度、过渡半径、点位 ID；与单轴 `HRIF_MoveRelJ` 是不同调用。
3. [官方 HansRobotAPI_Base.java](https://raw.githubusercontent.com/huayan-robotics/SDK_sample/main/JavaSDKSourceCode/src/com/hansRobotBaseLib/HansRobotAPI_Base.java)：`HRIF_MoveJ_base` 最终编码 `WayPoint` 且运动类型 0；`HRIF_MoveL_base` 编码 `WayPoint` 且运动类型 1；在线接口编码 `StartServo` 和 `PushServoJ`。这些不是项目中的 `MoveJTo` 的别名。
4. [官方 Sample_Servo.cpp](https://raw.githubusercontent.com/huayan-robotics/SDK_sample/main/CppLinux_SDK/src/Sample_Servo.cpp)：在线随动使用 `HRIF_StartServo` 后按周期发送 `HRIF_PushServoJ` / `HRIF_PushServoP`。示例的周期参数不是本项目已验证的周期能力；其粗粒度计时写法也不宜直接复制到实时调度器。
5. 项目本地厂家文档（本轮逐页读取相关正文，不把本地保存目录当成独立官方背书）：
   - [V6 通信协议 V1.0.19.1](../huayan-ros2-docs/docs/huayan-pdf/HuayanRobotV6控制通信协议接口_V1.0.19.1.pdf)：PDF 第184页/印刷173页：MoveRelJ；PDF 第192–194页/印刷181–183页：WayPoint；PDF 第209–210页/印刷198–199页：MoveJTo / MoveLTo；PDF 第215页/印刷204页：LongMoveEvent；PDF 第245–248页/印刷234–237页：StartServo / PushServoJ / PushServoP。
   - [Python SDK V1.1.1.1](../huayan-ros2-docs/docs/huayan-pdf/HuayanRobot_Library_Python_V1.1.1.1.pdf)：PDF 第373–375页/印刷361–363页：HRIF_StartServo / HRIF_PushServoJ 的版本限定。

来源冲突必须保留：官方 Java SDK 注释写长点动心跳不超过500ms；上述 V6 协议写不超过200ms。项目现有150ms属于更严格配置，但只在 `jogging` 状态工作。不能因仓库更新时间较新就覆盖具体控制器协议；本机控制器/SDK版本尚未读取验证，审查不擅自延长时限。

Python SDK 还区分 HR6.5.19 以前的更新周期参数，及之后按取值区分更新周期/刚度增益的语义；不允许把 `StartServo` 的参数、SDK的 boxID/rbtID 和原始ASCII的参数数量混用。V6协议“两个周期内更新”的规定也不能未经版本核实推广到所有后续固件模式。

精确坐标转换参照：Python SDK PDF第151页/印刷139页及第153页/印刷141页，`HRIF_Quaternion2RPY` / `HRIF_RPY2Quaternion`采用W、X、Y、Z顺序，RPY结果按Rx/Ry/Rz给出；官方Java Base同名方法也按W/X/Y/Z收发。此事实只说明接口参数顺序，**不能证明控制器MoveL内部使用本项目SO(3)插值或相同轨迹时间律**。

## 实际路径映射

| 入口/动作 | 仿真实际执行 | 真机实际执行 | 一致性结论 |
| --- | --- | --- | --- |
| 点位/程序 MOVEJ | `World._execute_move` → `SimRobot.move_j`：六轴共用五次时间曲线 | 默认 `real_move_cmd=relj` → `_real_rel_step`：每次选择误差最大轴、最多10°、等待停稳后下一段 | **目标可相同，中间关节/TCP路径明确不同** |
| 历史/显式配置 movejto | 仿真仍为上述同步曲线 | `_real_movejto` 发包前抛出明确错误，UI选项disabled | 未资格路径已阻止，不是实现了心跳 |
| 程序 MOVEL | 线性位置＋SO(3)姿态采样 → 本地IK → 每段 `move_j`、逐段停稳 | `_execute_movel` 直接拒绝 real | 跨界/近π/复合姿态对照通过；连续性仍未认证，无真机同路径比较对象 |
| 笛卡尔目标预览后执行 | 本地 IK 得到关节目标，再走 MOVEJ | 同上真机 MOVEJ 适配器 | “目标是笛卡尔”不等于“执行是直线MOVEL” |
| 跟随开始入框中心 | 复用普通 `_execute_move` | `_hand_available` 在real或存在真机链接时拒绝 | 仅复用仿真MOVEJ，不能称已打通真机 |
| 持续掌心跟随 | `HandFollow.tick`：最多30Hz附近点IK、关节速度限制、TCP速度上限300mm/s、≤10ms子步；写入 `sim.q` | 没有 StartServo/PushServoJ/P 实现，也不会向真机发这些指令 | **尚无真机随动链路，不是控制器Servo仿真** |
| 长关节点动（现有底层） | 单轴速度积分 | `LongJogJ` + `LongMoveEvent` | 指令意图相近；速度、加减速、松手停止延迟未标定 |

相关位置：[world.py](../sim/backend/core/world.py) `_execute_move`、`_execute_movel`、`_real_movejto`、`_real_rel_step`、`_hand_available`；[robot_sim.py](../sim/backend/core/robot_sim.py) `move_j/tick`；[robot_real.py](../sim/backend/core/robot_real.py) `_cmd_loop/jog_start`；[planning.py](../sim/backend/core/planning.py) `waypoints/inspect_path`；[protocol.py](../huayan_panel/core/protocol.py) `CMDS`。

## 需修根因，按风险排序

### MC01 / P1：默认 MOVEJ 不是同一运动策略

触发：至少两个轴需要移动，真机使用默认 `relj`。

仿真沿关节空间直线同步推进；真机适配器是关节空间折线，逐轴停走。几何离线例子：起点全0，目标 `[20,10,0,0,0,0]`。在J1=10°时，仿真经过 `[10,5,0,0,0,0]`，相对运动适配器先经过 `[10,0,0,0,0,0]`。用现有Elfin5 FK计算，这两个状态的TCP位置相差 **85.058057mm**。这是路径差异演示，不是实测定位误差；也不是对两条轨迹最大偏差的计算。

影响：用户看到的仿真轨迹不能充当默认真机轨迹。安全预检目前按各自模式选策略，因此该例不是“预检错用了同步路线”的证据；但跨模式拿仿真结果批准真机仍然错误。

最小建议：先把“同步MOVEJ”和“兼容逐轴到位”标成不同能力。若目标是真机同路径，必须选定并验证厂商普通 `WayPoint/HRIF_MoveJ`，统一速度、加速度、半径、坐标系与分支。若暂时保留逐轴协议，可另提供明确命名的兼容预演模式；不要偷偷让用户认为它等价于同步MOVEJ。

### MC02 / 原P1：MoveJTo未资格路径已阻止，未声称心跳通过验证

初始版本缺口：V6手册明确MoveJTo是“关节长按到点”，须配合LongMoveEvent；原 `_real_movejto`只发送一次后等待，而心跳条件为 `self.jogging`，这条路径不拥有该状态。

主代理修复后，本轮已执行实际方法的离线假传输用例：`_real_movejto`直接抛出“心跳尚未完成资格验证，禁止下发”的RuntimeError，假传输记录为空；SettingsPanel中对应option为disabled。旧保存值或绕开UI调用不能通过该执行方法发包。real_move_cmd默认仍是relj。

结论：本项“阻止未资格下发”的最小安全目标已满足。若以后启用，仍需独立长按运动所有权、协议期限内心跳、松手/撤销/断线停止和新鲜到位确认。不可简单伪造 `jogging`，也没有完成硬件资格验证。没有自动改用WayPoint。

### MC03 / P2：MOVEL姿态部分复核通过，段间连续性仍未认证

历史根因：`_execute_movel`曾对六个标量直接线性插值；固定XYZ、Rz从179°到-179°会经过0°。现改为实际调用 `interpolate_pose(start,target,fraction)`：线性位置、SO(3)对数/指数映射，数学目标为最短测地线，与四元数SLERP等价，而不是逐个Euler分量取短角。

已核验：实际MOVEL记录型IK输入为 `[180,-179]`，常见跨界不再经过0；样本数兼顾8mm平移和2°转角、下限2、上限120。纯转角40°、平移160mm、混合16mm/40°分别为20段，平移2000mm封顶120段。120段封顶意味着长距离仍可能超过每段8mm，不能把该采样密度描述成严格全局上限。

修复时间线：本审查曾复现从0到Rz=-179.9999°、fraction=.5时返回+89.999950001°，而最短路参考为-89.99995°；接近±90°俯仰的复合姿态也存在分支偏差。根因是 `rotation_error`近π分支从对称项恢复轴时丢失方向符号。主代理随后在反对称项范数大于1e-12时，以轴与skew的点积校正全局正负；精确π保留确定性轴约定。

复核结果：保留原8对姿态、每对5个分数点，共40个独立四元数SLERP参考探针，不修改原矩阵误差阈值1e-4。正/负179°、正/负179.9999°、180°以及复合/近俯仰奇异姿态全部匹配，`so3_reference_mismatches=[]`。只表示本轮覆盖范围内的姿态数学复核通过，不能扩展为整工作空间、真实IK或控制器轨迹认证。生产源码由主代理修复，本审查只更新报告与核对脚本。

每段仍调用 `sim.move_j` 并等待完成，保留段间零速与关节弦线；TCP在段内不由代码保证严格位于笛卡尔直线。

后续建议：另行统一时间参数化、消除非预期段间停顿。更完整预检应检查实际段内路径和对象，不只采样目标IK点。real-MOVEL仍关闭，不可用MoveJ串冒充控制器MoveL。

## 其他未验证差异

- SimRobot时长采用 `1.875*max(|dq|/vmax)` 和50ms最小时长，未实现控制器指定的加速度/jerk模型；`blend`参数没有实际过渡实现。同步几何并不能证明时间曲线一致。
- `planning.inspect_path`明确返回 `validated_hardware=False`，采用有限角度采样，不是连续碰撞证明；耗时估计也注明不含真机加减速/通信/停稳。
- Kinematics是URDF FK/数值IK。文件里记载过去两组静态PCS_Base偏置对账，但本轮没有取得原始标定证据，不能据注释推导整工作空间/动态轨迹一致。20mm固定参考偏置、实际安装角度、TCP/UCS、负载与线缆仍要对齐；仿真参考工具不是实际控制器确认TCP。
- 掌心检测频率、目标更新频率、上位机IK频率和控制器Servo周期是不同层。不能把摄像头15–20fps直接当作控制器Servo发送周期，也不能把30Hz上位机目标更新称作硬实时。
- 只监控末端工具/围栏不能证明整臂自碰撞安全；范围框可达也不等于所有IK分支、过渡路径可达。不得以“看起来不会碰到自己”替代路径及自碰撞资格验证。

## 最小修复顺序（建议，不是已实施）

1. 已阻止未资格MoveJTo下发；继续保留真机随动/MOVEL保护，不把禁用路径描述成心跳实现或现场认证。
2. 建立显式运动契约：kind、起始实测q、关节/位姿目标、TCP/UCS及标定版本、单位、速度/加速度/jerk或控制器能力、blend、IK分支、执行策略、停止策略。仿真和真机适配器都消费它；不在本次只读审查里新增生产接口。
3. 验证WayPoint/HRIF_MoveJ后再统一几何路线；修MC03后仍要单独验证MOVEL。
4. 如后续实施真机实时跟随，另建固定周期的Servo所有权与看门狗，以最新视觉目标生成受限、平滑指令，执行超时/丢流安全停止；不是把普通MoveJ反复发30次/秒。
5. 真机资格测试需后续单独授权：同一型号、TCP/UCS、负载、起点和运动参数；低速记录带时间戳的关节反馈与TCP，分别比较终点误差、路径/横向误差、姿态误差、速度、加速度、延迟、停机行为；阈值应由任务和控制器指标确定，不能在此臆定。

## 可重复的离线检查

运行：`.venv/Scripts/python.exe -B -X utf8 tools/check_motion_contract.py`。

初始审查完成8项核对。最新修复复核为12项通过、0项检查失败，其中独立四元数40个探针对照匹配。MC02状态为guarded_unqualified；MC03仍为partially_fixed，但 `orientation_fixed=true`，余项是段间停稳及连续轨迹未认证。输出仍为 `trajectory_equivalence=false`、`hardware_validated=false`。`--strict` 因MC01和MC03的保留缺口返回2，不代表姿态回归又失败，也不能为追求全绿删除未认证边界。

隔离方式：不导入World/main，不建事件循环、不建连接；仅执行实际纯数学/编解码代码，以及从AST提取的少量World方法，全部传入假传输、记录型IK和假执行器；禁用网络并不写入项目持久数据。记录型IK用例只证明插值输入，不证明真实IK可达或真机行为。脚本输出所读源文件SHA-256；多代理并发修改后必须重跑，不能把本报告视为持续有效的认证。

本报告及脚本只核对主代理修复，未修改生产源码、未正式打包、未重启，也未进行硬件验收。
