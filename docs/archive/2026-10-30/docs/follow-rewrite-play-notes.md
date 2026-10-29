# 分拣 / 井字棋 backend staged 交接

本轮仅写 tools/staged_*、tools/check_play_vision_rewrite.py 和本文。未修改 backend、未启动 World、HTTP、相机、真机、重启或打包。

## 精确 overlay 映射（必须成套）

| staged 源 | 隔离副本目标 |
| --- | --- |
| tools/staged_play_routes.py | backend/play_routes.py |
| tools/staged_robot_play_routes.py | backend/robot_play_routes.py |
| tools/staged_planning.py | backend/core/planning.py |
| tools/staged_play_vision.py | backend/core/play_vision.py（新增纯 helper） |
| tools/staged_play_main.py | backend/main.py |

main staged 唯一改动是 `create_robot_play_router(world,vision_workspace)`。主线若同时改 main，请只合入这一行，勿整文件覆盖主线改动。其余 staged 基于读取时的当前 backend 完整副本。

## 主线 runner 待注册

- allowlist 新增 `check_play_vision_rewrite.py`。
- runner 新增 `--staged-play`，过滤该参数后使用上表固定映射，严格只覆盖一次性 COPY 中的 backend 文件。可与 `--staged-follow` 同时启用。
- 命令：`.venv/Scripts/python.exe tools/run_offline_checks.py --staged-play check_play_vision_rewrite.py check_pick_place_program.py`。
- 新检查不导入 World/main、不使用 HTTP，只使用 fake world、真实 ObjectStore 临时文件、纯视觉数据和数值检查替身。测试运行状态在下文更新。
- 检查文件也支持在 runner 内按正确 backend 包名直接导入 staged 模块，无须等待 overlay 开关；仍须先添加 allowlist，禁止绕过 require_offline_copy。

## 依据与语义

旧 `RobotPlay.targets` 直接取 `piece['color']`，没有视觉依据；A 区按颜色列表展开后依次摆放。新默认 RGB 各 3，以 SystemRandom shuffle 分配 home，不依颜色排序；每件 72×72×24mm（旧 38×38×20），A 区 90mm 槽距、18mm 间隙。棋子 82×82×18mm（旧 70×70×16），A 间隙 8mm、棋盘 100mm 槽距间隙18mm。27 件等高密度模式保留38mm宽，不能对高密度不加区分放大。

分拣依赖注入同一 VisionWorkspace.snapshot()，只接受 camera / virtual 的当前像素检测结果。无需客户端伪造 binding；后端按标定后的基座 XY（容差 min(20,件宽/4)）、中心 Z（3mm）唯一匹配任务工件，强制 observed、独立 track_id、RGB 检测标签、当前会话/分辨率标定与两点独立复核。工件预设 color/rgb/name 不参与类别决策。帧号、时间、track ID、观测颜色和坐标、配置和标定被冻结进 plan / 返回 vision_evidence。

缺工作区、禁用、缺帧、过期(1.5秒)、导入图片、未标定、标定来源/分辨率失配、holding/expired、歧义绑定均立即失败，不进入IK、不等待相机、不发设备请求。source_key / instance / config / calibration 在计划发布、执行前及运行 guard 核对；后续帧号变化本身不撤销冻结计划。相机移动使 source_key 变化会要求重新识别。部分工件已在B时要求全部复位后重新采集，避免与旧B占位重叠。

B区按实际工件外形加10mm间隙容量检查，最多3个检测颜色分带；默认72mm工件每色一列三行。容量不足立即解释，绝不缩小碰撞包络。检测颜色若不是3/3/3，保持视觉结果，可能因容量不足而拒绝，不能拿模板颜色纠正视觉。

## 一直预检：已定位的可复现代码路径

9件生成72条 MOVEL，每条最多120个插值IK点，再校验实际 C1 轨迹及携带包络。旧90秒 checkpoint 仅覆盖外层循环；首段4个seed×(等待位+取料位)IK未传 solver 已支持的 deadline/cancelled；inspect_path 的每个碰撞样本和物体遍历没有 checkpoint。旧 await shield(worker) 没有请求级超时，线程没返回就一直 planning。失败时 message 仍可能为空，没有进度。上述是代码与假模型可确定复现的路径；未访问现场，不把它声称为截图现场唯一原因。

新实现将 deadline/cancelled 传到每次IK（含solver重启），inspect_path逐样本/物体合作取消，保留原采样密度、碰撞/限位/轨迹和携带校验。请求 wait_for(shield(worker)) 限90秒，超时/取消不发布plan，busy直到线程实际退出才释放，禁止重叠求解。无法强杀不合作线程：请求可按时返回但busy可能保持，状态保留失败原因；真实solver已有循环中止检查。未通过删除校验提速。

## 分拣 UI 接口建议（交主线，不修改 FunctionsPanel）

- `POST /api/play/program` 原请求兼容，分拣 criterion 仅 color；kind 会明确拒绝。失败沿用422 detail或 `{ok:false,why}`。
- `GET /api/play/program/state` 新增 progress `{phase,step,total,line,kind}`、elapsed_s、timeout_s、can_cancel；原 planning/active/message 保留。
- `POST /api/play/program/cancel` 仅设置预检取消事件，不停止其他功能、不改变IO。响应 planning 反映线程是否真的退出。
- 独立于正在等待 generate 的请求轮询 state，显示行号/步骤、耗时、取消按钮；失败立即清除UI忙碌并显示why/detail；后端仍planning时禁止重复生成。
- 前端生成前可提示“采集A区→标定→识别→预检”；无观测时后端会直接返回原因，不能自动反复重试。
- 预检成功显示 vision_evidence 的来源、帧号、颜色与 track/object 绑定；不能用场景色统计冒充识别计数。
- 原37个点（WAIT+A18+B18）、9循环、逐件返回等待位、工具IO顺序不变；井字棋UI由其他worker处理。

## 井字棋 worker 新契约已接入

已读取 `web/src/chessMatch.js`。带 `symbol:'O'|'X'` 的 chess 请求只选一枚该符号的A区棋子，cycles=1、5点、8运动段，返回 symbol/slot/group/action；不在一个计划内夹带AI下一步。服务端校验5圈4叉、合法槽位、O先且交替、终局、已落子实际位置与B区一致。用户/AI随机角色由前端决定，后端只校验轮次。未传symbol保留旧双枚接口兼容，不能用于新前端。排序请求不能携带symbol。

新检查覆盖单O计划、O后单X计划、完整9步平局、错误符号/轮次/占位、缺棋子/棋子移位、旧双枚兼容。仍复用现有 generate_pick_place/parser/PreparedMotion/runner；没有新增自动执行或真机通道。

旧前端 `cancelWaiting()` 曾只取消fetch等待。2026-10-29已只读核对Pascal新实现：已接 `POST /api/play/program/cancel`、progress/elapsed_s/timeout_s/can_cancel轮询，并等待planning=false解除后台状态；后端state/cancel契约保持不变。后端新增接口不依赖前端按钮即可保证请求90秒截止；求解器内部也接入deadline/cancelled。

旧 `check_robot_play.py` 无视觉注入的排序成功用例已不符合新真实观测契约；旧 `check_play_scene.py` 直接调用 sort 的瞬移成功用例也已被显式拒绝。主线若回归这两组需迁移用例，不能恢复RGB伪识别来迎合旧断言。

## 验证状态

- 6个新增/修改 Python 文件 AST 解析通过；staged main 与读取时main比对，确认唯一变更为视觉实例注入。
- 已执行纯helper断言：9件空间唯一绑定、预设RGB被忽略、B区边界/不重叠、过期/图片/未标定失败、冻结依据/新帧与换源处理、输入不变，全部通过。
- 已执行真实数值 RobotModel（无World/设备）：20ms deadline 的高迭代IK于31ms返回，预置cancel事件的IK近0ms返回，均未报告收敛。证明solver已有中止能力，本轮修复的是调用链未传参数；单次数值测量不构成现场实时性保证。
- 新离线检查现共28个用例（含子用例），覆盖geometry/evidence/planning/cancellation、单枚棋步、真实像素链路及40/30工具真实physics。
- 2026-10-29 runner `--staged-play check_play_vision_rewrite.py`：28/28通过，52.050秒；报告 `output/checks/huayan-fix-checks-gc771t1h.json`，详细日志由报告指向。
- 40/30工具指 mountHeight=40mm、cupHeight=30mm；使用真实sim_tool.model_for、真实IK、evaluate、prepare_movel、inspect_path与carried_clearance，fake world仅替代World与设备。默认72mm九件分拣72运动段耗时32.469秒，单O/单X各8运动段耗时9.360/7.844秒，全部预检通过。无physics mock，无关闭ground/fence或删减轨迹检查。
- 分拣physics的输入为合成PNG，经真实VisionWorkspace.analyze HSV颜色检测、calibrate的四点+两点独立复核、平面映射和track，9件颜色/坐标唯一匹配；没有将场景color作为观测输入。该证据限定合成像素，非现场相机。
- 沙箱内第一次/第二次测试各180秒退出；额外栈转储证明卡在asyncio创建事件循环的socketpair.accept，尚未进入fixture/IK。经自动审批后在沙箱外运行同一受控离线runner取得上述有效结果；未启动服务或World，未访问HTTP/相机/真机。沙箱失败报告保留，不计作产品通过或IK超时证据。
- 追加完整9步棋局逐枚真实physics通过：`ELFIN_PLAY_PHYSICS_ONLY=1` 下同一runner共2项用例（九件分拣+完整棋局），93.872秒；报告 `output/checks/huayan-fix-checks-266qh8k5.json`。棋步按 `(O,0),(X,1),(O,2),(X,4),(O,3),(X,5),(O,7),(X,6),(O,8)` 依次规划，每次仅一枚/8运动段，使用上一步已验证的终点关节与B区放置结果继续下一步。默认82mm棋子在A区90mm槽距、B区100mm槽距和已落子逐渐增多时，所有真实曲线/障碍物/持件预检均通过。该检查不运行程序/IO，只提交已验证放置到fake world以衔接下一步。

## 主线分拣 UI 精确字段和用户操作

1. 用户布置默认RGB各3件，全部处于A区；每次重新布置或回A后重新采集，不复用旧场景绑定。
2. `/api/vision/workspace/config`：`enabled:true,input:'virtual'|'camera',colors:['red','green','blue'],height_mm:24,mapping:true`；ROI覆盖全部A区、工件不能接触ROI边缘；现有完整virtual参数沿用配置，不能用场景颜色替检测标签。
3. 通过现有实际彩色帧入口采集，读取 `/api/vision/workspace/state` 的 `instance_id,config,calibration,result,stale,error`。virtual帧提交仍为现有 `{session,image_base64,view_key,captured_at}`，不要把场景对象数组当图像发送。
4. 缺标定时用户提供6行 `points:[[pixelX,pixelY,baseX,baseY],...]`（前4点构成凸四边形，后2点独立复核）及 `plane_z:4,max_error_mm:2` 给 `/api/vision/workspace/calibrate`；plane_z=4仅适用于当前默认4mm料区顶面，场景被移动后须输入实际平面高度。画面完整覆盖工件；没有标定时显示“尚未能真正分拣”，禁止假完成。
5. 标定后等待新的一帧；检查 `result.source`、`source_key`、`frame_seq`、`captured_at`、`calibrated`、`objects[].{id,status,color,rgb,mapped.xyz_mm,mapped.coordinate_space}`。后端只接受当前1.5秒内帧，status必须observed，映射空间必须base_xy_planar_estimate。config.height_mm应对应真实件高24mm，否则Z中心绑定会拒绝。
6. 发 `POST /api/play/program`：`{group,action:'sorting',criterion:'color',speed:15,clearance:120}`，如已示教等待位可附 `wait:[X,Y,Z,A,B,C]`；不要传客户端colors/bindings，后端用当前工作区观测建立唯一绑定。缺依据的why/detail直接展示，不自动重试预检。
7. 生成时独立轮询 `/api/play/program/state`，展示 `planning,progress.phase,progress.step,progress.total,progress.line,progress.kind,elapsed_s,timeout_s,can_cancel,message`；取消用 `POST /api/play/program/cancel {}`，等planning=false才解除服务端占用状态。`failed/cancelled/cancelling/complete`为可出现阶段，parsing/checking为进行中。
8. 成功后显示 `vision_evidence.{source,source_key,frame_seq,captured_at,bindings}`，每个bindings键是object_id，值含 `track_id,color,rgb,xyz_mm`。按检测color统计，明确颜色与模板色不一致也以检测为准；不能只显示RGB模板数量。
9. 人工确认后沿现有程序执行器发 `/api/play/program/run {plan_id,source}`；points仍37项、cycles=9，保留点位复核与失败后重新采集/复位操作。

## 必须场景验的缺口（不是本轮离线通过声明）

- 现有相机视角是否能完整看见9个72mm工件；光照、HSV阈值、背景/托盘干扰、ROI裁切是否导致漏检或相邻轮廓合并。合成图像通过不能替代这一项。
- 六点标定的实际平面、图像坐标与base坐标是否一致，独立复核误差是否满足阈值；内外参畸变、相机位移、工具遮挡和抓取偏差尚不由单平面映射证明。
- 当前virtual视角key随相机/机器人位姿变化；若相机随机器人运动，source_key变化将使本版严格依据guard撤销计划。主线必须在场景验中确认固定视角，或另行明确冻结观测与移动相机的生命周期规则；不能静默放松来源绑定。
- 实际场景中除模板道具外的额外障碍物、当前关节起点、等待位、工具40/30以外尺寸和手工移动的料区均会改变完整路径结果；离线默认模板通过也不能覆盖这些状态。
- 三维渲染中的82mm棋子视觉形状与保守盒包络、吸盘接触点、抓取/释放后的slot和3D对象同步，仍需场景观察；本轮只做纯离线数值，不启动真实World或执行设备。
