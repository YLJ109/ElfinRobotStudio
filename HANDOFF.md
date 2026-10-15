# 华沿 Elfin 点位仿真与监控系统 · 新对话上手指引

> 给**新会话**的第一份读物。读完这一页就能动手，不用重新考古。
> 位置：`D:\front-back\HuaYanRobot` · 最后更新：2026-10-12（真机在线实测过一轮）

---

## 0. 动手前先读这条：三道隔离闸，一道都不许拆

**这个系统能真的让一台通电的六轴机械臂动起来。** 2026-10-12 我自己制造过两次事故，都是因为测试脚本连上了控制器：

| 事故 | 后果 |
|---|---|
| `tools/check_backend.py` 连上真机 | J6 被拖走 2.04°，末端 **DO1 留在通电** |
| `tools/cdp_probe.mjs` 在真机就绪态跑 | J6 被拖到 **+17°**，超出授权幅度 |

两次都已把机器分步退回原位（J6 回到 -101.863°，其余轴 0.000 漂移），并立下三道闸：

1. **`HUYAN_NO_REAL=1`**（`sim/backend/core/config.py: no_real()`）
   进程级禁止连控制器：`World.connect_real()` 直接拒绝、启动强制落回仿真。
   `check_backend.py` 在 **import app 之前**置上（World 在 import 时就建好了）。
2. **脚本前置闸**：`security_check.py`、`cdp_probe.mjs` 开头检测 `mode==real` / `motion_granted`
   就**直接退出**（`cdp_probe` 的闸放在启动 Chrome 之前），除非显式 `--allow-real`。
3. **`tools/hy.py: Guard`**——我自己发任何指令必须走它：
   `set_speed()` 拒绝 >5%；`ask()` 前检查控制器实际 `override`、只允许 `axis=5`(J6)、单步 ≤5°。
   实测三条全部拦得下（40% 速度 / 动 J1 / 单步 9°）。

### 权限口径（用户两次明确过，别搞反）

- **用户操作 = 不设限**。软件里所有"替操作者做主"的闸已被**删除**：
  `motion_guard_on` / `motion_axes` / `motion_max_step_deg` / `motion_speed_max_pct`、
  设置页那张卡、后端 `_guard_motion`/`_guard_axis` —— 全没了，别加回来。
  `precheck()` 也只回警告不拦指令（越限位/会撞 → 照发 + 记日志 + 返回 `warn`）。
- **我跑脚本 = 硬设限**：只 J6、≤5%、单步 ≤5°。
- 连续点动按住上限是 **15s**（不是限制，是标签页崩了别一直动的兜底）。

---

## 1. 启动与验收

```bat
run_sim.bat            :: 后端 127.0.0.1:8710 + 打开浏览器
run_sim.bat dev        :: 后端 + vite 5173 热更新
```

手工起后端：
```bash
cd /d/front-back/HuaYanRobot
PYTHONIOENCODING=utf-8 ./.venv/Scripts/python.exe -m uvicorn sim.backend.main:app --host 127.0.0.1 --port 8710
```
前端改完必须构建（后端托管 `sim/web/dist`）：
```bash
cd sim/web && npm run build
```

**验收一律用脚本，别用肉眼猜**（2026-10-12 基线，全绿）：

| 命令 | 覆盖 | 当前 |
|---|---|---|
| `./.venv/Scripts/python.exe tools/check_backend.py` | 后端接口/状态机/安全/分段逼近 | **62/62** |
| `./.venv/Scripts/python.exe tools/check_datasheet_fields.py` | Data Sheet 字段对齐（防"帧里有数界面不给看"） | **15/15** |
| `node tools/dom_audit.mjs` | DOM 量测：padding/溢出/折叠/单按钮/设置页结构 | **18 项全通过** |
| `node tools/cdp_probe.mjs <url>` | 残影行为（仿真模式） | **9/9** |
| `./.venv/Scripts/python.exe tools/security_check.py` | 来源校验/凭据/脱敏/注入 | **28/28** |
| `node tools/shot_cdp.mjs <outDir> name=url` | 真实时间等"后端在线"再截图 | — |
| `./.venv/Scripts/python.exe tools/check_ik.py` / `check_fk.py` | 逆解 6 例 / 正解对账 | 6/6 |

> `check_backend.py` 会先备份 `sim/backend/data` 再退出还原 —— `events.jsonl` 是用户唯一的
> 真机审计第一现场，**绝不能被测试覆盖**。

---

## 2. 硬件事实（全部本机实测，不是文档抄的）

- 控制器 **192.168.0.10**，点对点直连（无 DHCP/交换机）。型号 `E05-1195_Pro`，SN `PF067250009`。
- 端口：`10003` IF 命令（严格串行）、`10004/5/6` JSON Data Sheet（约 20Hz）、`80` 示教器、`8080` 诊断、`22` SSH。
- 帧协议：`MAGIC(LTBR)+总长(4LE)+净长(4LE)+payload`；指令 `名,p1,...,;`；回执 `名,OK,...,;` / `名,Fail,码,说明,;`。
- **就绪链（2026-10-12 逐条打通）**：
  ```
  FSM 7 --Electrify--> 14 --StartMaster--> 15 --> 24 --GrpEnable--> 33
  收回：GrpDisable → (等 FSM 落定) → BlackOut
  ```
  - `ConnectToBox` 在 FSM=14 实测回 **20018** → 不在自动链里，只留手动可选。
  - **`StartMaster` 才是 14→24 那一步**（协议 4.1.5）。我此前记的"网络侧推不动 14→24"是错的，
    是指令表漏试了一条。断言"做不到"之前先把指令表过一遍。
  - 下电后 FSM 会经过 **6** 这个过渡档，此时 `Electrify` 回 20018 → 必须等落定。
  - `GrpDisable` 后立刻 `BlackOut` 也回 20018 → `power_down()` 里已加 `_wait_fsm_settle`。
- **控制器正在运动时再发 `MoveRelJ` 回 20018**。这就是"残影没跑满就停"的根因：
  分段逼近必须**发一段 → 等 `moving=0 且 InPos=1` → 再发下一段**（`world._real_rel_step`）。
  1% override 下 4.5° 要跑十几秒，等待超时要给够。
- 实测生效过的：`SetOverride 0.05`（Data Sheet 与 `ReadOverride` 都回 0.05）、
  `MoveRelJ` J6 ±2°（rtt 15–47ms，其余五轴 0.000 漂移）、`BlackOut` 48V 48.06→8.87→0.05V。
- **FK 与真机 `PCS_Base` 对账 ≤0.09mm / 0.001°**（补 20mm 法兰参考点偏置后）。零位法兰高 1195.003mm。
- 单位口径：URDF rpy 是固定轴 XYZ（= `Rz·Ry·Rx`），Three 侧必须用 Euler order `'ZYX'`；
  基变换 `(x,y,z)→(x,z,-y)`。
- **未取证**：`MoveJTo`（协议 p198，本机没验过，默认不走，设置里可显式切）；
  吸盘/气阀与 DO1-3 的映射、末端 4 路 DI 哪路是到位信号（测过，全 0，没测到电位变化）。
- 官方完整错误码表 `HuayanRobot_ErrorCode.docx` 仓库内缺失；中文 PDF `pdftotext` 抽不出 CJK
  （字体没 ToUnicode），英文手册 `tools/en.txt`、协议 `tools/proto.txt` 可读。

---

## 3. 代码地图

```
sim/backend/main.py        FastAPI 入口 + 访问边界（回环/token/Origin 守卫）+ GZip
sim/backend/core/
  world.py                 唯一状态源，50Hz 主循环（1429 行，仍是上帝对象，见 §6）
  controls.py              主按钮状态推导：任何时刻只有一个合法动作
  telemetry.py             回放环形缓冲 / 影子孪生偏差 / 就绪体检 12 项联锁
  robot_real.py            真机链路 + FIELD_MAP（64 个字段的对照表）
  robot_sim.py             仿真驱动
  kinematics.py            FK/IK(牛顿步+回溯线搜索)/胶囊体距离解析解
  safety.py                80/90/100 分级 + 围栏/自碰撞/地面/摆放物
  objects.py               3D 摆放物物理（重力/落地/漂浮/碰撞）
  program.py               自定义脚本语法解析 + Runner
  ai_plan.py               智谱 glm-4-flash 计划 + 故障码解说（输出必带"AI 推测"标注）
  config.py                设置持久化 + 迁移 + no_real()
  eventlog.py              事件日志 + CSV 公式注入转义
sim/web/src/
  App.vue                  布局/路由/键盘/假手三用途分发
  scene.js                 Three 场景（2.5D 机位、HUD 刻度环、轨迹、健康着色、省电降帧）
  store.js                 双通道：3D 走非响应式、UI 20Hz、遥测 1Hz
  theme.css                三层令牌 + 切角/角括号 + 灰底报警纪律
  components/              TopBar(单主按钮+三灯) NavRail StatusStrip LogDock ObjectProps Spark
  panels/                  Sim Power Work Points Program Ai Objects Telemetry Logs Settings About
huayan_panel/              桌面 PyQt6 面板（协议层 protocol.py 被本系统复用）
tools/                     上面表格里那些脚本
```

**快照分两个通道**（别把新字段随手塞进 20Hz）：
`FAST_KEYS` 每帧（4.7KB）、`tele` 每秒（`/api/state` 与 WS 首帧强制带）。
`/api/trend` 是 1Hz 温度/电流/偏差曲线缓冲，前端按需拉。

**路由**：`#/sim` `#/power` `#/work/{points|program|ai|objects}` `#/tele` `#/logs` `#/settings` `#/about`

---

## 4. 界面口径（用户定的，改动前先对齐）

- **一个按钮**：标签/颜色/可点性全由后端 `ctrl()` 推导（未连接→`连接控制器`；FSM<14→`上电`；
  <24→`启动主控`；<33→`一键就绪`；≥33→`取消就绪`；有故障码→`复位清错`）。
  按错了不猜意图，返回"现在该按什么"。
- **许可 == 就绪+使能态**，不是可选择的档。`/api/perm` 已删，只剩 `/api/perm/ack` 换一次性凭据。
- **残影 = 另一台半透明假手**（不是拖尾帧，这个我误解过、返工两次）：
  拖滑块只摆假手、真机原地不动；点执行真机开过去；
  **六轴都进 ±0.3° 且站住 0.5s 且 `InPos=1 && moving=0` 才自动收**（判定在后端 `_tick_preview`）。
  同一台假手三种用途，优先级：残影 > 回放游标 > 隐藏。
- **读数诚实**：没读到就 `--`；仿真数据永远带"仿真"徽标；超限标红不折回；报错必带码。
  链路断开时关节角是**未知**不是零 —— `joints.known=false`，表里显示 `--`，三维停在最后一帧。
- **样式是一等公民**：padding ≤10、禁双层 padding、少 margin、只用 flex gap；
  不引远程字体（Bahnschrift/雅黑/Cascadia 都是本机自带）；每步用 DOM 量测+截图验证。
- 键盘：`1-7` 切页 · `f` 切坐标口径 · `r` 复位视角 · `空格` 收残影 · `Esc` 急停。

---

## 5. 踩过的坑（别再踩）

| 坑 | 结论 |
|---|---|
| bash heredoc 写中文 | **会被转码破坏**（今天把 `world.py` 弄出语法错误）。含 CJK 的改动一律用 Edit/Write 工具 |
| Git Bash 的 `/tmp` | Python/Node 把它解析成 `D:\tmp`。落盘用项目相对路径或 `process.env.TEMP` |
| `chrome --headless --screenshot --virtual-time-budget` | 虚拟时间在 WS 送达前就截屏，永远截到"重连中+空面板"。用 `tools/shot_cdp.mjs` |
| 内置 browser-use 截图 | in-app 浏览器 0×0 视口，截不了 |
| `Reply` 对象没有 `.hint` 属性 | `hint` 只在 `as_dict()` 里现算；直接 `rep.hint` 一走失败分支就 AttributeError 把接口打挂（已修，见 `_fail_text`） |
| 把 `click()` 放进 `setTimeout` 里派发 | CDP 求值上下文中 change 事件不走 Vue，勾选框看着变了、界面不重渲染。改成"同步 click + 另起一次 evaluate 轮询" |
| 用 `undefined` 和 seq 比较 | 非遥测帧没有 `telemetry_seq`，`undefined !== -1` 会把已收到的遥测覆盖成空。必须先判 `if (m.data.telemetry)` |
| 帧率用 0.5s 插值缓冲算 | 重连时积压帧同瞬盖戳，能算出 516Hz 的假数。单独用 2s 到达窗口 |
| 空闲缓存键漏了"机器人在动" | 正在下落的箱子会被缓存冻在半空 |
| 测试脚本自己污染状态 | 断言"默认关"之前必须显式归零，否则测的是上一轮残留 |

---

## 6. 还没做的（按价值排）

**要硬件才能验**
1. 真机跑满残影（分段修复目前只有离线假链路证明）
2. 手拖示教 `GrpOpenFreeDriver` 松闸验证 —— **Basic Free Drive 无重力补偿，臂会坠，
   必须有人手扶**；`IsFreeDriveMode` 回读已确认能读到（现在稳定 0），
   末端 Free 键 ↔ `EndButton[0..3]` 哪一路还没标定；Advanced（带补偿）本机是否支持未取证
3. IO 通道标定（吸盘/到位信号）
4. TTS 语音播报从未被验证（无头环境听不到）

**纯软件**
5. 摆放物地面拖拽 Gizmo / 网格吸附 / 高度滑块；工位布局存盘切换
6. 面板宽度拖拽 + 记忆；初次入场面板默认折叠成角标
7. 视觉回归像素 diff 基线（现在只截图肉眼看）；4 小时浸泡测试（堆/RSS 不涨）
8. 事件日志防篡改 hash 链；`pip-audit` / `npm audit` 纳入自检
9. 首包 636KB（gzip 183KB）未做到原计划的 ≤460KB；`world.py` 1429 行只拆出了
   `controls.py`/`telemetry.py` 两块，没继续拆
10. 手柄未接线（用户曾喊停）；亮色主题档未做

**安全提醒**：智谱 key 曾被贴进对话，建议轮换；它只存在项目根 `.env`，不进代码。

---

## 7. 记忆位置

- 用户偏好：`C:\Users\Administrator\.qoder-cn\memory\MEMORY.md`
- 本项目：`C:\Users\Administrator\.qoder-cn\projects\D--front-back-HuaYanRobot\memory\MEMORY.md`
  其中 `huayan-test-isolation-rules.md`（隔离闸 + 不设操作者限制口径）、
  `huayan-pendant-ready-precondition.md`（StartMaster 那条修正）是本次改动后更新过的。
