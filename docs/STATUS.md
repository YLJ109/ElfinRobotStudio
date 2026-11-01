# 当前状态

## 本轮（2026-10-31 22:20–23:05）：预检提速 2.1–2.4 倍 + 预检进度条

用户判断"仿真不需要这么重的加固和预测"，授权直接优化。定位到延迟来自**次数 × 每次几何量**，逐项拆解后改了 6 处（判据一字未改，全部是去重复与剪枝）：

| 改动 | 文件:位置 | 效果 |
|---|---|---|
| 求距加球-球下界剪枝（下界 > 当前最小间隙才跳过） | `safety.py:_object_signed_gap` | 单段 60°/轴 235ms → 47ms |
| 4×4 矩阵乘法逐单元展开（原为推导式，被调 172 万次） | `kinematics.py:_mat_mul` | 4.13μs → 2.31μs |
| 连杆 visual 常量矩阵预计算（原来每次重算 rpy_to_mat） | `kinematics.py:__init__` / `moving_ground_clearances` | 去掉每连杆 5 次乘法 + 3 次三角函数 |
| 曲线分支改用算好的 spacing（原来固定 1°，把仿真 2° 丢了） | `planning.py:inspect_path` | 曲线采样减半 |
| MOVEL 去掉逐节点重复 evaluate（整条曲线预检已覆盖每个结节） | `planning.py:prepare_movel` | 每段评估次数减半 |
| MOVEL 结节点 8mm→15mm、2°→3°（仅仿真；真机不变） | `planning.py:prepare_movel` | steps 120 → 67 |
| 地面间隙在胶囊已明显离地时跳过逐顶点求解 | `safety.py:evaluate` | 远离地面时省 8 万顶点遍历 |

**实测（`tools/check_start_latency.py --repeat 3`，取最小值）**：

| 任务 | 改前 | 改后 | 变化 |
|---|---|---|---|
| 分拣（9 件） | 16.0 s | 6.56 s | −59% |
| 绘制 | 14.0 s | 6.59 s | −53% |
| 写字 | 3.0 s | 1.32 s | −56% |

**新增预检进度条**：后端本来就在按 `{phase, step, total, line, kind}` 上报，但 UI 只有一句"正在排程…"。现在 `StatusStrip.vue` 增加一条全局进度条（`role="progressbar"`，读 `snap.robot_play.progress`），`RobotPlayProgram.vue` 补上一直定义了却没用到的 `<progress>`，`FunctionsPanel.vue` 的井字棋排程显示百分比。只读既有字段，不新增轮询、不伪造进度。

**回归**：前端 1359/1359；`check_full_link_collision` 15/15、`check_collision_recede` 20/20、`check_startup_block` 10/10、`check_drawing` OK、`check_ai_skills` OK。

**不变的部分（明确未放松）**：真机采样 1°、MOVEL 8mm/2°、物体碰撞全连杆口径、退离判据、穿透/加深/穿侧的拒绝对照——全部保持原样，所有负例仍然失败。

## 上一轮（2026-10-31 22:00–22:20）：Tabbit 浏览器真实目视验收，补齐"三维无法目视"的缺口

上一轮用的是 `chrome-headless-shell`，拿不到 WebGL，三维画面全黑，所有"UI 待验"项实际上没有目视证据。本轮改用本机 **Tabbit 浏览器**（`D:\ProgramData\Tabbit Browser`，Chromium 155）以 `--headless=new --remote-debugging-port=9333` 驱动，实测 **WebGL 2.0 / ANGLE (Intel UHD D3D11)**，三维可见。

**验收方式**：`tools/qa_cdp.py tour` 依次点击 10 个导航项并逐页截图（`output/qa-shots/`，25 张），逐张人眼核对；另用 `tools/qa_first_move.py` 做 UI 层首动延迟实测。

**逐页结论**
- 视角（UI05）：机器人**完整不裁切**，上方留白充足，地面网格清晰可见。此前把 TCP 轴向指示线误判为"手臂顶裁"，放大裁剪后确认不是问题。
- 围栏（UI06）：残影透明度滑块 30%，落盘 `settings.ghost_opacity=0.3`；围栏 `fenceMat.opacity=0.008` 是绿色态的设计值（只留 `fenceEdges` 0.28 线框），非缺陷。
- AI（AI01/AI02）：单一助手，无 Tab；执行/暂停/模型/发送同排且位于输入框下方；空状态不堆字。
- 程序（PROG02）：只剩 编程 / 程序库 / 语法；语法页内容完整（语法说明 + 指令表：SPEED8 / MOVEJ / MOVEL / DOUT），不空白。
- 场景（SCN01/SCN02）：三 Tab 无"批量"；现场文件列表撑满余高可滚动；演示·01–10 共 10 套场景齐全。
- 有趣（FOL01）：输入源只剩 手掌 / 鼠标。
- 分拣 / 井字棋（SIM03）：无独立预检入口，有"正在排程…"即时反馈。
- 系统 / IO（UI03）：仿真态 IO 页是"仿真别名演练"，已明确标注"仿真别名仅用于演练"，不违反模式隔离。

**性能实测（真实数字，如实记录）**
- 单次 `inspect_path` = **391 ms**（26 采样 × 10 连杆 × 9 物体），热点 `_object_signed_gap` 占 80%。
- 首动耗时：分拣 **16.0 s**、绘制 **14.0 s**、写字 **3.0 s**（`tools/check_start_latency.py`）；UI 冷启动写字实测 **25.6 s**。
- 结论：瓶颈是**预检次数 × 单次耗时**的串行累积，不是单点慢；`_OBJECT_EVAL_BUDGET=7200` 已生效。要实质提速需并行预检，或给 `capsule_object_gap` 加 AABB 粗筛（安全无损方向）。此项**未改动**，留待决策。

## 本轮（2026-10-31 21:10–21:55）：修掉"仿真一打开就被碰撞锁死"，并推进剩余项

**用户报障**：打开仿真即被阻止，日志"安全余量达到红色/碰撞级：已自动停止，需人工复位"+「box#4 压到 elfin_link4」。

**根因（已定位到具体数据，不是猜测）**：现场 `backend/data/objects.json` 里有一个默认小盒 `box-a`，位于 `xyz=[1,2,15]`、尺寸 `10×20×30`，正好落在机器人转座（`elfin_link1`，胶囊 z 73.5–193.2、半径 57.3）内部，实测余量 **−13.8 mm**。全连杆判定一上线，这个"藏在转座里的小盒子"每帧都被判为真实穿透 → 触发 `_on_alarm` → `sim.paused=True` + `queue_state=aborted` + "需人工复位"，而 UI 没有对应复位入口，于是"无缘无故被阻止"。

**修复（4 处，均有离线检查）**
1. `world._on_alarm(moving)`：仿真中**机器人静止且无任务在跑**时的相交，只报一次"可沿脱离方向退出"，不再暂停、不再要求人工复位；运动中出现的碰撞仍照旧自动暂停（SIM05）。
2. 命中口径统一：`objects.step(dt, 承托包络, contact_ids, 命中包络)` —— 物理承托仍用保守包络，**命中判定改用与预检/安全层完全相同的 `object_capsules`（全可动连杆、非膨胀）**；`preview_safety` 同步。此前"膨胀包络蹭到"会被当成真穿透。
3. 安全缓存键加入 `objects.persisted_generation`：移走障碍后告警立刻消失，不再挂旧结果。
4. 落位即拦截：`workspace_routes.edit_scene` 与 `/api/objects`(add/update) 在写入前用 `World.robot_overlap()` 检查，重叠时直接返回「「X」与机器人 Y 重叠，请换个位置」，而不是放完再报警。

**数据处置（非删除）**：把 `box-a` 从 `[1,2,15]` 平移到 `[-450,350,15]`（空地、不与其它物体/围栏冲突），物体本身保留。移后实跑 `/api/state` → `level=green`、`obj_hits=[]`。

**证据**：`tools/check_startup_block.py` 10/10；`check_full_link_collision` 15/15；`check_collision_recede` 20/20；`check_drawing` OK、`check_ai_skills` OK；前端 `npm test` **1359/1359**（较上轮少 4 项，是 `check-web-launch.mjs` 里退役的 3 个生产页校验 + 1 个构建身份校验改为新契约断言）。

**同轮推进**：SIM04 手掌微步与路径预检改全连杆（含预算内自适应采样）；SIM03 去掉任务独立预检步骤、加点击到首动实测埋点（代码级：分拣 13.4s、绘制 11.1s、写字 2.5s，均为真实 IK/碰撞计算，无等待）；UI05 默认正面视角 + 机器人上移露出地面；桌面端复用 8710 的 30s 超时改为秒级明确报错；`run_backend.ps1 -Web` 生产模式正式下线；CAM01 相机契约审计修 3 处（关闭超时状态、标记面板错误态、检测启动失败不再当成功）。

**未验**：浏览器三维画面（无头 Chromium 拿不到 WebGL，画布全黑，无法目视 UI05）、桌面真实窗口、实体相机、真机。**未打包**（按用户要求）。

## 本轮并行实施（2026-10-31 20:00–21:10）：14 项代码落地，验证到离线/前端全量级

用户要求"全部开始做"，按文件边界并行处理后落地以下改动。**未做浏览器逐页点击、桌面真实窗口与真机验收**，不据此宣称 41 项闭环。

- AI03 组合绘制：`Drawing.preview` 新增 `prefix_joints`（几何起点=已检查示教点；`origin`=预检期间不得移动的实时反馈，两者分离）；执行顺序改为**先预检、通过后才移动到组合起点**，预检失败不再拖动机器人。`ai_skills._approach` 复用 `inspect_plan`/`execute_plan` 唯一执行器与既有安全检查。证据：`tools/check_ai_skills.py` 3/3、`check_drawing.py` 31/31。
- RUN01 旧网页托管：`backend/main.py` 移除 `StaticFiles` 挂载与根页面缓存分支；8710 `/` 现为 404 JSON，`/api/*`、`/ws` 行为不变。实跑实测：`/`=404、`api/state|tools|build`=200、5173=200。
- RUN01 连带回归（已修）：桌面端原依赖 8710 提供页面。`desktop/backend_entry.py` 在桌面进程内单独挂载 `web/dist`（并摘掉 `api_only_root` 路由以免截走首页），`backend.spec` 补 `hiddenimports=['starlette.staticfiles']`。仅 AST 与内存 ASGI 探针验证，**未打包、未启动真实窗口**。
- PROG02：删除 `web/src/programDebug.js` 与程序面板调试 Tab/断点/500ms 后台轮询；语法页保留且非空白；真实运行停止/暂停继续保留。后端 `/api/program/debug` 路由仍在但已无前端调用方。
- UI04：型号入口收敛为 Elfin 5（`visualAssets` 白名单只剩 elfin5，旧本地偏好残留会被改写为 elfin5）；其它型号资产仅解除引用，未删除文件，点位/程序/场景数据未动。
- UI06：`FencePanel` 范围页新增残影透明度滑块（0.1–0.7，默认 0.34），复用唯一 `ghost_opacity` 与既有 settings 落盘，不改实体模型/范围/碰撞。
- SIM06：目标失败与取消收敛到单一通知点，按 `plan_id+epoch+started+revision` 身份去重；顶部横幅不再重复，目标失败不再写 `world.error`；原始原因保留在 `failure.detail` 与后端日志。
- SCN01：修正折叠「现场文件」时侧栏宽度塌陷（错误规则作用在横轴而非纵轴）；三 Tab、去批量、列表撑满滚动本已具备。
- SCN02/VIS01：场景导入拆"读文件/解析/结构校验"三段保护并明确"当前场景未改变"，网络异常与导出失败不再静默，新建失败保留名称；视觉识别失败状态全局可见，取点/标定按 `stale/fresh` 门控，旧图结果标注"过期·不用于控制"。
- AI01/AI02：空计划不再渲染"0 步"计划框、失败原因去重；执行/停止/暂停/继续位于输入框下方同排；`allow_model` 仅显式发送时逐轮传递，无自动调用、无静默降级，密钥仅以 `key_present` 布尔出现。
- UI02/UI03：设置页控制器 IP、自动连接、真机运动指令、真机速度、调试台只读指令仅在真机显示；合并重复说明并统一"仿真"口径；真机失败与资格反馈全部保留。
- FOL01/FOL02：删除键盘/手柄输入源；开始按钮在无相机时可点，顺序为**先回中心、后按需开相机**，相机失败不回滚中心动作；手掌与绘制范围参数未改动（中心/尺寸/方向/默认值保持冻结）。
- SIM01/SIM05：仿真近距余量不再拦截（真机分支零改动，`g<=0` 判据保持）；新增 `contact_profile`/`recede_verdict` 几何退离判据，放行"间距单调变大"的脱离，拒绝加深、穿到另一侧、新增相交面与真机路径。顺带修复 `safety.py` 缺 `import math` 导致盒体穿透每帧 `NameError` 的真实崩溃。

验证：前端 `npm test` **1363/1363 全过**；`check_ai_skills` 3/3、`check_drawing` 31/31、`tools/check_collision_recede.py` 20/20，另 safety/hand-follow/movej-axes/mesh-ground/chess/drawing/text 等 9 组定向通过。**未执行**：浏览器逐页点击与截图、桌面打包与真实窗口、实体相机、真机、外部模型。

已知遗留：① 桌面端源码态若复用 `start.bat` 的 8710 会 30s 超时（该进程按新需求只给 API），需先 `start.bat stop` 让桌面起自有后端；② `run_backend.ps1 -Web` 生产页校验已无调用方且必然失败，应连同 `Test-ProductionWeb` 一并下线；③ 碰撞退离仍只对末端采样生效，围栏只监测末端，MOVEL 未接退离；④ 其余 PLAN 项（GAME01/DRAW/TEXT/SORT/TASK/PROG01/LIB01/CTL01/CTL02/UI05/CAM01）仍属"已改待同版实际验收"。

## 最新恢复：先整理全部问题（2026-10-31）

用户最新要求先整理全部问题，已在 `docs/PLAN.md → 用户全部问题清单` 汇总41项，结构化源为 `project.json → user_requirements_closure_20261031`。保存已落修改，暂停新增实现；不声称全部完成。新增要求包含仿真余量不拦截/碰撞可退出、报警一句话、残影透明度、吸取闪现、AI输入行按钮/仅助手、只留Elfin5、真机专属显示隔离、场景独立物体Tab/去批量/文件列表拉伸、程序去调试/语法简化、视觉简化、仅5173网页入口。

已落的最新变更：AI单助手与输入行按钮/模型入口、组合编排（缺Drawing.prefix_joints，尚不能交付）；相机中心旧会话隔离、持件同姿态显示/释放实际锚点、场景与视觉简化。安全全连杆及仿真近距门禁修改正在集成，退离未闭环。其它型号/旧网页托管/程序调试/残影参数/重复报警尚待改。详见统一清单，不以历史测试总数替代当前状态。

2026-10-31 写字最新：当前前端1323项全过、同版构建通过（writing-relief-{frontend,build}-20261031.log）；默认一零九，科技青描边，暗紫填充跟随真实笔尖逐步出现；已写区域0.5秒由平面升为2.5mm浅浮雕，不改机器人轨迹。预览仅本地字形，不调用完整预检；同一已就绪计划严格绑定起点/配置/工具/场景/有效期复用约0.425ms。仿真同一轮廓合并连续曲线，超2mm偏差的尖角保留逐段；仍复用原Runner/唯一执行器，首次执行需要准备，不宣称秒开动或所有笔画都不停。连续文字6项（暂停/继续/取消）与普通绘制27项通过。最新浅浮雕效果尚未实际截图复核：浏览器用户已切至点位操作，未抢占重跑。

2026-10-31 本轮反馈收口：当前前端1319全过、同版构建通过，日志parallel-feedback-{frontend,build}-20261031；统一5173/8710热更新，旧18765～18768服务已停，49条旧快照/缓存路径及16份staged实现移除，正式数据与失败证据保留。文字默认一零九/暗紫/细字体；本地图形预览实测137ms（当时你好/青色），可一次执行排程，编辑/取消/失败撤销排程。点击开始跟随可自动开选中彩色相机并启用检测，取消/并发/迟到会话检查已补齐；新检测帧立即发送。未改手掌和绘制范围。真实摄像头当前关闭，现场流畅度和最新暗紫实际书写截图尚未复验，不标全项目完成。

2026-10-31 主服务405修复：运行后端未加载 /api/sim/task-tool，POST落入静态资源处理返回405。确认空闲仿真后通过项目启动器重启已核验仓库后端，HUYAN_NO_REAL=1；8710和5173无动作探测均HTTP200。证据 output/checks/chess-tool-main-route-20261031.json。18767为旧冻结测试副本，不作为最新入口。

2026-10-31：仿真井字棋开局自动切换双吸盘，保留原自定义尺寸；并发点击、失败及写字页迟到清理保护已检查。隔离后端4项、前端相邻回归及生产构建通过，实际网页装笔→开始→双吸盘/可落子已截图复核。证据见 project.json 的 chess_auto_tool_20261031；真机未做自动换工具。

最新：正式场景库已加入十套分拣/井字棋/装盒/拼摆/搬运模板，原场景和当前布局不变；正式UI十入口及截图已核对，证据`output/demo-scenes-20261031/133943/`。前端1299全过，最新桌面包gi5fjc6w与native04启动/窗口/60轮切页通过，截图已看；实际场景编辑与导入恢复通过。视觉后端GPU图像识别三色通过，但综合负载、新版AI历史/错误UI及若干剩余流程仍待，不宣称全部验收。真机按用户要求暂不做。详见计划源最新continuation及HANDOFF。

2026-10-31 演示库：十套程序已保存且隔离零位独立执行10/10；按用户选择保留旧四点，追加演示01～10（P6～P15），正式API及磁盘核对一致。程序库改为可点击列表与代码预览，正式网页十条显示及快速切换验证、截图实际查看通过。证据/备份在`output/demo-library-20261031/131645/`，安装核对`points-installed.json`、界面`program-library-list.png`；构建及10项回归日志`output/checks/program-library-list-{build,regression}-20261031.log`。真机未验，手掌/绘制范围未动。

## 2026-10-31 仿真稳定版：正在实施，未交付

当前继续：场景分拣/装盒/拼摆新增显式scene来源，复用原执行器及真实吸放到位逻辑；不伪装为视觉检测，camera模式失败不降级。隔离35项取放（含原相机负例/真实数值取放）及20项装盒通过，前端1297全过，生产构建通过。最新18764生产快照已实际完成三色分拣3/3、暂停继续和三格装盒3/3；截图已查看并存于`output/stabilization/*actual-completed-20261031.png`。拼摆、更多负例与桌面打包正在验收。分拣点击到运行13.738秒，仍待优化，不能称秒回应。

手掌延迟：结果客户端原来每次读后固定等待100ms，现改为33ms周期串行读取；彩色相机显式开启直接启用检测，同会话并发启用合并。相关56项通过；未改变手掌/绘制范围、原采集TTL或真机资格。用户已关闭真实相机，未自动重开；截图本地推理未检出手掌，不能作为识别成功或实机流畅度证据。已释放4套本轮旧隔离服务，当前仅保留18764验收副本。主8710已在空闲仿真状态加载同版修复。

最新分拣交互：独立“摆放”和“开始/停止”，保留“暂停/继续”。50项定向检查及生产构建通过；隔离生产页18763实际点击摆放，生成3件物料，关节与执行ID不变，开始随后可用；截图已查看：`output/stabilization/sorting-placement-separated-20261031.png`。这仅证明摆放与执行入口分离，不冒充完整分拣取放验收。仿真允许用户显式开启真实摄像头；未由助手打开实际相机。

最新前端全量1294/1294通过。后端完整首轮保留5组失败记录；逐项修复退役工艺检查、默认100%速度/有效时间契约及合法放置球体夹具后，受影响检查均通过（基础64、执行17、调试20、速度6及工作区集成）。原失败与每次复验均保留，链接在计划源；这不代表剩余实际UI/打包已验收。

当前目标是现有仿真功能正常、准确、简化；手掌和绘制范围冻结不改。已完成的具体修复、当前证据和未验范围以`project.json → simulation_stabilization_20261031`为准。初始20项前端失败保留，首轮修正1293全过；最新导航改动需最终复验。桌面38项通过。后端完整隔离检查、当前源码实际UI、性能与打包闭环尚未完成。

已实际确认井字棋机器人取放O/X及2D即时落子；旧UI快照不作为最新源码完成证明。井字棋预检阶段已缩短，尚未达成或宣称首个运动1秒内。此前相机夹围栏导致视角突变的误改已撤销，绘制预览不再自动切换观察机位。

2026-10-31 仿真速度/电脑摄像头：新默认倍率100%（配置、仿真对象、无状态UI和绘制表单），保留明确保存的自定义值；轨迹准备期间改变倍率及绘制运行中调速不再使仿真任务失效，原曲线和关节速度上限仍保留，真机绑定不变。可见相机窗口按后端会话自动获取设备目录，后台重启后不再只剩仿真；构造/隐藏不枚举、不自动采集。实际后端与网页确认Chicony内置相机、编号名称、仿真最后，网页速度100%；六初始化接口200/JSON。Windows启动器隔离Python控制台，防止uvicorn重载CTRL_C_EVENT取消PowerShell流水线；隔离信号回归通过，当前服务已恢复。前端相机/启动42及绘制42通过；仿真26/跟随44/绘制27报告 `output/checks/huayan-fix-checks-rx724xf2.json`；绘制运行中50→100仍执行完成补验 `output/checks/huayan-fix-checks-sjhkcw6a.json`。编译输出 `output/checks/camera-speed-ui-build-20261031/`；未覆盖生产dist，未开启实体相机，真实手掌继续由用户自行验证。

2026-10-31 手掌检测后续回归：实际后端统计显示检测持续执行且无worker错误，但页面可能因“检测帧领先预览帧”直接丢弃结果。该门槛已在真实CameraFrame消费者中复现失败并移除，保留会话/序号/原始采集TTL校验；新增无手掌有效推理仍统计帧率的回归。对照旧前端worker，后端恢复VIDEO连续跟踪（阈值仍0.5），内部模型时钟严格递增且不替代采集时效。模型及pipeline隔离检查13项通过，报告 `output/checks/huayan-fix-checks-o4_4fk00.json`；前端相邻检查144项通过。开发后端曾再次退出，启动器新增独立输出/错误日志，已恢复8710；确切原退出原因未有日志证明。用户自行实测真实手掌，不宣称硬件检测闭环通过。

2026-10-31 最新修复：可见的工作站彩色图像流自动开启后端手掌检测，两处检测勾选已移除，检测状态在视觉窗口内显示，页面隐藏/卸载及迟到启用清理保留。前往中心不再因仿真检测加载错误而提前退出，实际掌心采样仍受原新鲜度与会话校验。绘制四朝向、世界中心旋转、局部宽深、后端实际轮廓/边界已对齐跟随；修复跟随围栏夹取误用未旋转尺寸造成的中心偏移。AI多余勾选说明移除，外发许可边界未放开。正常网页5173、后端8710已恢复，六个初始化接口均200/有效JSON；自有静态样例5175/18740和测试标签已关闭。

本次前端定向230项通过（`output/checks/follow-drawing-frontend-20261031.log`），独立输出目录编译通过（`output/checks/follow-drawing-ui-build-20261031/`）。后端跟随44项/检测pipeline12项/静态模型1项通过（`output/checks/huayan-fix-checks-reedtl72.json`）；绘制27项通过（`output/checks/huayan-fix-checks-fr9ivn0r.json`）。用户明确真实手掌自行测试；未验证真实相机或真机、未声明完整实际浏览器跟随闭环通过。

2026-10-31：摄像头来源列表改为按已确认的物理设备身份合并别名，显示连续编号和名称，仿真摄像头列在最后；同名但不同身份的设备保留。前端定向离线检查通过，实际界面与实物设备未复核。

2026-10-31：浏览器相机跟随准备曾将扫描快照误判为失败，导致未进入检测及仿真回中；已按实际扫描响应修正，并用真实响应形状的假设备复现与回归。前端定向检查通过；真实相机和实际浏览器识别、仿真回中仍待用户现场复核。

2026-10-31：用户要求观察相机可超出安全围栏，自由缩放、平移和环绕。源码已移除观察相机的围栏夹取及广角补偿，旧受限视角不再自动恢复；物体摆放边界仍沿用原规则。离线场景交互检查通过，实际浏览器/GPU画面尚待复核。

更新：2026-10-30。本轮用户反馈的源码修复与实际验收正在收尾，尚未最终交付。以下为当前进度；上轮构建、测试总数和完成声明仅保留为历史。

## 本轮实现与实际证据

- 控制页单层分类；有趣含跟随、井字棋、绘制，分拣六任务。语法页、AI执行/停止与暂停/继续、仿真直接执行和简短缺点位拒绝均已实现。
- 交互盒按选择方向整体移动中心，保留半径、高度与尺寸；可明确恢复初始范围。此前“方向不移动中心”是已被用户后续要求替代的阶段实现，不能当当前行为。
- 左右吸盘四切换按钮已有实际UI回读证据；直接改坐标的假整理与工艺录制正式入口已退役。历史录制只读兼容保留，旧动作409；编程能力保留。
- 桌面相机权限blanket deny/origin尾斜线问题及compact挂载已修；安全fakevideo实际1280×720预览、停止轨道、零上传/零推理已验，未打开真实相机。蓝色HSV与正式场景调色匹配缺陷已修。
- `source-complete-tasks-current` 中handling、kit、pattern与完整A10均已成功。整轮报告仍为failure，原因是后续renewal验收驱动把旧Runner aborted误认作新manual任务失败；原失败保留，不能把整份报告写成通过。
- `source-chess-current` 已实际完成双方两枚取放与RGB三件取放（3/3，100分），截图已查看。点位旧arrival修复已落地，不再列为待开发，但最终同版actual仍统一复验。
- 绘制上下文/按钮响应已修；负X大框中六个280毫米级动作已由实际Runner完成（te8khuxu）。共享默认与最终原生视觉/暂停取消仍待同版复核。不能以已写driver或CPU几何测试代替真实绘制效果。
- 当前正式后端完整检查 `ix09wov4` 运行中；前端共享默认合并后重跑，桌面38项已通过。旧117/1237/36及更早数字只覆盖其绑定源码。
- 网页入口为 `start.bat web`，生产HTML/资源与后端源码、前端构建双SHA匹配才就绪；不使用Vite跳转，不自动结束旧服务。

## 仍待完成

最后actual绘制/RGB、最终包、真实浏览器与网页、匹配清单及源码恢复包需绑定最终源码闭环；不提前声明完成。真实机器人、IO、真实相机、手机HTTPS跨机、GPU利用率与外部AI质量未验。APP009/010、YOLOE及新增原生适配保持候选。

## 上轮绑定版本记录（历史，不代表本轮终态）

2026-10-30：用户实操发现语法空白、AI控制缺失、交互盒位置、棋局等待、相机权限、网页入口与多余操作文案问题。此前“全部完成”结论不覆盖这些缺陷；正在按新要求修复并重新实际验收。下方为上轮记录，不能冒充本轮通过。

### 上轮状态

核对日期：2026-10-30。位置优先、启动J6=0的授权迁移与真实行为修复已完成；正式后端116/116、前端1205/1205、桌面29/29全部通过。同业务版本native组合负载、review与恢复复验已通过；最终包A10完整10件亦通过，本期must软件与适用本地验收完成；源码恢复包已完成逐文件SHA256及ZIP CRC核验。

### 上轮冻结版本与正式检查

- 桌面快照 `desktop-source-82qduod3`，身份 `output/renewal/desktop-build-source.json`。后端 `6fa89acc66a0ca26b151f66d8496d53dcf7cc44ec29ee3004f5331f6a1fe435b`，前端 `14c96472bb2dbc5fbc1b65a1051af6a9a5b97d7501da172d4fda08432a1b3969`。产物 `output/desktop-release/win-unpacked/JiuShuRobotStudio.exe`，构建 `output/renewal/letter-final-desktop-build-v4.log`；未发布。
- 正式完整后端116组全部通过：`output/checks/huayan-fix-checks-ciqgv5x8.json`。七组迁移定向101项全部通过：`output/checks/huayan-fix-checks-38flo8f6.json`。原始七文件字节、SHA与逐项安全断言映射：`tools/legacy_contracts_20261030/manifest.json`；原955j531c/qcx39rvb失败证据保留，不改写历史结果。
- 前端1205/1205：`output/renewal/camera-mount-continuity-after-all.log`；桌面29/29：`output/renewal/letter-fresh-wait-desktop.log`。原限位、速度、精度、时效、坏解及恢复要求保留；显式旋转仍检查实际SO3精度，不要求位置模式全程锁姿态。
- 当前实际浏览器 `output/renewal/ui-verified-browser/browser-review.json`：鼠标实际23.54mm、键盘11.22mm、停止稳定、启动J6从5°到0°；`authorized-*.png`已实际查看。prq任务/恢复/负载为未改模块证明，范围见 `output/renewal/authorized-unaffected-workflows.json`，不冒称当前完整包全部重跑。
- 当前同业务双SHA负载 `output/renewal/packaged-authorized-combined-retry/result.json` completed、errors为空且正常退出：60.0257秒实际提交28.58775帧/秒，后端单核30.38094%（整机3.79762%）、工作集96985088字节、保留堆增长126716字节；GPU利用率未知。`packaged-authorized-recovery/result.json` 3启动/2重启通过；`packaged-authorized-review/result.json` 真实120秒、缩放与18行匹配通过，截图已查看。最终快照仅3个显式验收驱动差异，业务双SHA未变，审查见 `output/renewal/final-desktop-driver-provenance.json`。18项清单已附加验证。A10-v3原验收helper早于15fps间隔断言新帧而失败，原证据保留；有界1.5秒等待真实下一帧已定向通过 `output/renewal/letter-fresh-wait-directed.log`，未改生产TTL或来源/时效门禁。`packaged-letter-A10-v4`完整10件已通过；最终清单/浏览器已验，源码恢复包已完成逐文件SHA256及ZIP CRC核验。

当前源A10完整流程 `output/renewal/source-letter-A10-v2/result.json` completed、errors为空、正常退出：475932毫秒（约7分56秒），10/10、carried/confirmed均10、无持件、Runner finished，10格独立实际位置误差0；终态截图已查看。最终浏览器 `output/renewal/ui-authorized-final-acceptance/browser-review.json` 18行匹配、真实载入WAIT 3通过，authorized-final-restored-editor/authorized-final-acceptance-current截图已查看，自有页与服务已释放。最终包A10-v4完整复验已通过，源码恢复ZIP/hash已核验。

## 已实现能力与历史阶段证据

以下保留功能实现和此前绑定版本证据；段内“当前包/最终”仅指其历史阶段，当前版本以顶部为准。

## 已实现与实际界面证据

- 显式应用工厂、每app唯一World/资源/设置/存储/资产域和失败释放；导入不写盘。CART外部运动绑定不可变单次计划、轴门禁和同反馈新鲜/停稳证据；实际生产浏览器预览执行成功，截图 `output/renewal/screenshots/cartesian-reached-final.png` 已查看。真实PCS/TCP资格不由仿真替代。
- 四输入共用控制核心、停止/断流/旧连接/权限与未知状态门禁；左右八动作互斥与档案已实现，生产UI逐项回读，假IO负例通过，真实气路未操作。
- APP001受控静态GLB、摆放/变换/锁定、共享撤销重做及保存重开已实现。实际错误文件拒绝、穿地失败后抬高→缩放1.2→锁定→保存重开均有反馈；对象错误归属/晚回执与跨页等待已回归。截图 `output/renewal/screenshots/glb-reopened-locked-final.png` 已查看；不是任意网格/动画/贴图导入。
- APP002颜色/真实轮廓形状分拣已实现；真实JPEG圆度缺陷先复现后修，源码实际取放100分。当前切片 `packaged-current-acceptance` 的分拣→运行记录仍在验收，未写整轮通过。
- APP003真实双方取放、忙时锁输入、取消保留和复位已验；阶段证据 `output/renewal/source-chess-v4/`、`output/renewal/packaged-renewal-final/first-attempt/`。原分拣驱动失败保留，与棋局通过分开。
- APP004三有限关卡完整仿真通过，原速关2/3已纳入正式全量；最新实际UI关1完成56.73秒、1/1件、100分、无持件，`output/renewal/screenshots/handling-completed-final.png` 已查看。终态提示已修，当前后端状态不再被旧“正在执行”覆盖。
- NEW006有界脱敏运行记录/同run_id/只读API与导出已实现，安全程序实际步数和末行一致。当前切片分拣记录导出仍在进行。
- NEW008统一备份差异、新目录副本与显式桌面停止重启已实现；阶段三次启动 `output/renewal/packaged-recovery-final/result.json` 通过。旧切片 `recovery-final-acceptance` 三次启动PASS但曾发现summary编码字符串；当前包已修并在下节完成可读差异与三启动复验，不继承硬件资格或任务。
- 默认相机仅用户授权前端预览，检测关闭不上传/不推理；工作站共享原始采集、独立手掌进程、失败显式无效、有界最新帧。官方静态正反例和打包worker已验，未开真实相机。远程单向WebRTC短期配对/撤销/断流已实现，本机合成ICE/DTLS/SRTP互通 `output/checks/elfin-rtc-loopback-kx8c2m8n/report.json`；手机HTTPS/权限/跨机未验。

## 第二批与工程支撑新增证据

- APP005/006已实现按当前识别找物、ROI计数缺多错位判断与真实拒绝边界；像素9/API通过 `output/checks/huayan-fix-checks-dmdzqcif.json`。
- APP007/008复用唯一RobotPlay、不可重用预览、实际视觉绑定与格位计划；布局、容量/缺料/未知/来源/TTL/取消、完整1件及配方布料17项通过 `output/checks/huayan-fix-checks-60cewyps.json`。单色10件为38mm，普通对象保留，public sorting每色≤9未放开。
- `output/renewal/practical-source-ui-v3/result.json` 四任务实际UI通过，kit/pattern各1件，截图已查看。v4驱动函数clone失败保留；v5真实配置请求429饥饿修前 `j1xqefnk` 三失败，修后priority5/workspace41/tasks9共55项 `w39zmoum` 通过。该source阶段不是当前0mm8o9ua包终态证据，该阶段已由下节当前包实际验收补充，旧失败证据保留。
- NEW003/004已在原StatusStrip/就绪条件复用后端状态和定位入口，无新控制器；实际相机日志遮挡footer已复现，层级修复13项定向通过，当前包125%/200%无遮挡截图已查看。
- NEW005现有遥测性能页具显式有界同场景前后采样/本地导出；`output/renewal/source-diagnostics-review-v1/` 实际下载前后各3样本，125%/200%截图已查看，该旧导出缺真实build身份，不作为最终同版证明；修后当前包真实身份导出与无遮挡截图见下节。物理采集/真实显示率、GPU/进程占用未测值不填零。
- NEW009组合既有仿真/只读排障已实现；NEW010复用About与 `/api/acceptance`、构建双SHA核对清单，无匹配manifest不编造当前验收；source About无manifest实界面已验，最终匹配清单待负载终态后填充。

最新h5qcily4后端不变、前端c7db26a仅修overlay CSS/编译回归。旧0mm8o9ua前端00a3ce的workflows/recovery为未受影响行为证据，不冒称完整前端SHA相同。摄像头特写改为等待实际提交帧与核对视角，camera-mounted-closeup实际镜头/双吸盘/关节截图已由主代理查看；旧2次动画回调截图不冒充同视角。受限桌面测试回环失败日志与自有包装进程中断保留，当前25项提升权限复验通过。

## 当前包实际验收

当前0mm8o9ua包已完成实际验收：`output/renewal/packaged-final-workflows/result.json` completed、errors为空且正常退出。找物同帧/计数缺件、缺料/容量拒绝、kit实际2/2、pattern取消0/1后新任务实际1/1均通过。A图案10红38mm的真实像素绑定与预览通过，仍为0/10，未执行完整10件，不能写成10件全部完成。NEW005导出已核对真实双SHA、工具、会话与窗口身份；125%/200%浮层无遮挡截图均已查看。

同一档案 `output/renewal/packaged-final-workflows/journal-result.json` 正常退出且验证通过，真实chess/sorting/kit/pattern记录标签及导出正确。`output/renewal/packaged-recovery-current-final/result.json` 三次启动完成，firstRestartVerified及normalSaveAndSecondRestartVerified均通过，可读备份差异截图已查看。

当前生产浏览器 `output/renewal/ui-current-browser-final/snapshot.json` 在1280与1024窗口实际验证无输入禁用、默认零上传与就绪导航，`current-browser-*.png` 已查看；自有浏览器已置空，自有隔离服务正常停止。原 `packaged-load-final` 已完成实际60秒指标、停止回读及资源释放正常退出，但验收驱动误要求WAIT逐步日志，整轮未通过。Runner内部处理WAIT不会产生该日志；修后驱动使用有限200次SPEED 10/WAIT 0.5循环共400真实步、正式仿真就绪及实际SPEED日志，无运动或IO。其后overlay黑填充为真实产品缺陷，已修子组件CSS并加编译作用域回归；实际透明轮廓截图已查看。eydo首次packaged-overlay-final启动请求失败仅留下plain object，确切路由未知，失败保留。驱动配置HTTP预算1.8秒短于生产优先等待2秒，改为配置请求5秒并保留错误上下文，未改帧TTL或断言。最新combined负载与soak结果见下节。

## 最终组合负载与清单

`output/renewal/packaged-final-combined/result.json` completed、errors为空、正常退出；独立视觉子窗、单实例与资源释放均通过。60次开关后资源稳定，堆增长170556字节（约166.6 KiB）。60090.9毫秒采样期间，场景实际提交26.127帧/秒（不是显示帧率或动画回调率）；识别帧龄P50/P95/最大83/153/348毫秒，HTTP延迟4.8/31.6/182.8毫秒。后端CPU占单核28.55%（8核整机约3.57%），末次工作集99975168字节（约95.34 MiB），停止确认123.8毫秒。GPU利用率未测，不能由提交率推导。

18项功能的双SHA匹配清单仅附加生成的acceptance.json，未改变EXE或业务代码；运行时当前匹配18行，不授予真实设备资格。最终 `packaged-final-evidence-ui/result.json` completed、errors为空、正常退出，matches_current=true且18行；实际导出前后各3样本绑定当前verified双SHA，125%/200%截图已查看。18721生产浏览器18行/当前SHA截图已查看，保存为 `output/renewal/screenshots/final-browser-bound-acceptance.png`，自有页面已置空、隔离服务正常停止。finalcombined视觉子窗/仿真主画面透明轮廓截图均已查看。源码恢复包已生成并逐文件SHA256/ZIP CRC核验，为 `output/renewal/source-recovery-20261030.zip` 及 `.manifest.json`，只含源码，不含实际用户数据、环境密钥或依赖；应恢复到新目录，不覆盖当前工作区。基线commit/tag已核对77729ef。

## 完成状态与验证限制

本期must软件实现、正式检查、构建及适用本地仿真/UI闭环完成。最终 `output/renewal/packaged-letter-A10-v4/result.json` completed、errors为空、正常退出，practical completed、cleanupErrors为空；477120毫秒、10/10、carried/confirmed均10、无持件、Runner finished，10格独立XYZ误差最大0。完整10件终态及缩放后主画面已实际查看，无遮挡。main的stop为finally清理，完成快照保留，不是任务失败。

- 固定路径源码恢复包 `output/renewal/source-recovery-20261030.zip` 与 `.manifest.json` 已核验941个文件SHA256及ZIP CRC。最终产物校验见 `output/renewal/final-artifact-verification.json`。
- 真实机器人、IO、电平、TCP/负载/包络、控制周期、停稳/停止距离、真实相机、手机HTTPS跨机、GPU利用率与外部AI质量未验；仿真不授予现场资格。
- APP009/010、YOLOE及新增原生适配继续为候选，不提升承诺。

## 历史证据

原六旧合同失败、runtime夹具过期、旧构建与驱动失败记录均保留。用户授权迁移后，真实启动归零/显式姿态精度、同TCP腕回正与有界续算已修复并通过当前完整116组。历史105/7、h5/prq及其他阶段数字不能替代当前版本证明。
