# 根目录重构与控制链审查

## 本轮范围

- 用户授权：删除 PyQt6 桌面工程和 `huayan_panel`，将 `sim/backend`、`sim/web` 迁至根级 `backend`、`web`，继续审查并完善。
- 保留仿真业务模式和既有 API，目录迁移不开放新真机运动能力，不连接或操作硬件。
- 保留全部现有未提交更改；移除的桌面工程先在本地备份。用户运行数据原样迁移并对比哈希。

## 执行顺序与验收

1. 迁移协议至后端，迁移目录并清除活动代码中的旧包依赖。
2. 更新启动入口、静态资源、数据定位、离线检查和前端测试路径。
3. 检查真机控制/停止以及跟随/碰撞链路，已证实缺陷逐项添加回归后修复。
4. 执行隔离后端检查、前端测试和构建，检查新入口；记录未覆盖项。

## 边界

- 不提交、推送或发布；不卸载机器上已安装的 PyQt6。
- 历史审查报告保留原路径作为历史证据，不把旧通过记录当成本轮验收。
- 企业级是持续的工程要求，不代表目录改名后已经获得现场安全认证。

## 状态

结构迁移和下述缺陷修复已落地；最终验收汇总见文末。这里只覆盖本轮重点链路，
不是整库无遗漏审计，也不是企业级交付或真机现场安全认证。

## 已完成的结构重构

- 根级 `backend/`、`web/`，不保留旧 `sim/` 包或 `huayan_panel` 兼容壳。
- 协议归 `backend/core/protocol.py`，后端不再依赖 GUI 包或临时修改 `sys.path` 引用桌面工程。
- `backend/paths.py` 统一定位后端、运行数据、前端、模型和工具资源，不依赖工作目录。
- `start.bat` 统一启动／开发／构建／重启／停止入口；环境检查不再安装 PyQt6。
- `backend/requirements.txt` 固定本轮直接依赖；`pyproject.toml` 声明 Python 范围与包边界。
- `tools/check_project.ps1` 统一本地结构检查、前端回归／构建、隔离后端回归。
- 72 个源文件／脚本／活动说明中的机械路径引用已迁移；历史审查文档保留原路径作为历史证据。
- 未卸载虚拟环境中原有 PyQt6，不删除用户配置、点位、工具状态或日志。

## 重点问题清单

下表位置指迁移后实际修复代码。修复已经写入源文件，不是待执行建议。
“软件已修”不代表解决了物理停机的资格验证问题。

| 优先级 | 位置 | 已证实的问题与触发条件 | 修复与回归 |
|---|---|---|---|
| P0 | `backend/core/protocol.py:35`；`backend/core/robot_real.py:711` | 无范围字段跳过数值检查，参数可夹带逗号、分号和第二条命令；只读命令同样受影响 | 独立验证每个协议字段；无范围仍验证类型／有限值／整数性；`check_control_hardening.py`、`check_real_stop_transport.py` |
| P0 | `backend/core/world.py:1977` | 通用原始指令可绕过专用运动、使能、IO 和倍率入口 | 仅允许协议表中的明确只读命令，`GrpStop` 转统一停止；拒绝旁路写指令；`check_control_hardening.py` |
| P0 | `backend/core/world.py:1849`、`:1874` | 回执前的旧空闲帧可确认停稳；未停稳仍按 ACK 返回成功 | ACK 后捕获时间＋帧号屏障、同连接确认；`ok` 依赖真正确认停稳；`check_control_hardening.py` |
| P0 | `backend/core/world.py:1869` | 停止中或停止失败后仍可能追加运动 | 停止未知闭锁；运动、使能／就绪、危险 IO 和真实倍率修改不能绕过；并发停止共享任务；取消请求不取消停机任务 |
| P0 | `backend/core/robot_real.py:808` | 跟随调用方超时会删掉队列中的停止，命令循环随后跳过它 | 链路持有独立停止任务，绑定原命令会话与 writer；停止不被普通队列失效或 caller 取消静默清除；15 项假传输回归 |
| P0 | `backend/core/world.py:2429`；`backend/core/robot_real.py:328` | 状态流改变 epoch，旧代码会放弃向仍在线的原命令连接下发停止 | 区分命令会话身份与状态有效代次；只向原命令会话停止，禁止重连重放；World 与传输两侧均有回归 |
| P1 | `backend/main.py:321`；`backend/core/cameras.py:551` | 到达时用客户端年龄重建采集时间，漏算传输延迟 | 按会话＋原始帧号查服务端采集单调时间，并把原始时间传至提交检查，处理耗时也不能续期；前端新增 `frame_seq`；9 项访问与时效回归 |
| P1 | `backend/core/hand_follow.py:520`；`backend/core/world.py:2666` | 一批内部步只在最终姿态查碰撞，安全起终点可掩盖中途碰撞 | 候选提交前逐内部步检查弦线；拒绝候选不发布，保留已安全接受的小步；物体／地面中途碰撞集成回归 |
| P1 | `backend/core/hand_follow.py:428` | 掌心有效但张合缺失，被解释成新的深度回中指令 | 保持上一次有效局部深度，丢掌仍走明确回中策略；新增纯类与实际模型回归 |
| P1 | `backend/access.py:7`；`backend/main.py:70`、WebSocket 入口 | 仅比较 Origin 与请求 Host，不能排除任意 Host 的本机 DNS 重绑定 | HTTP／WebSocket 共用可信 Host；未知网络对端不冒充本机；远程 Host 需显式配置，令牌边界保留 |
| P1 | `backend/core/config.py:66` | README 声称首次默认离线，但无配置时 `auto_connect=True` | 首次默认不连接；不覆盖用户明确保存的自动连接选择；结构回归核对常量 |
| P1 | `web/package.json:29`、`:30` | 官方审计报告旧 Vite／esbuild 开发依赖存在已知漏洞 | 核对插件兼容范围后升级 Vite 6.4.4、Vue 插件 5.2.4，更新锁文件；升级后官方完整审计为 0 项 |

### 修复代码与可重复检查

关键修复的完整代码已在上述源文件及函数内，以下是可直接执行的负例回归入口；
所有有状态后端检查必须经隔离运行器，不能直接导入原工作站 World。

```powershell
# 正确：原始命令注入、控制旁路、停止闭锁和回执屏障。
.venv\Scripts\python.exe -B tools/run_offline_checks.py check_control_hardening.py check_real_stop_transport.py

# 正确：传输延迟不能续期旧手势，起终点安全不能掩盖中途碰撞。
.venv\Scripts\python.exe -B tools/run_offline_checks.py check_access_freshness.py check_hand_follow_safety.py check_follow_path_integration.py

# 正确：一次完成本轮统一验收；不安装依赖、不连接硬件、不上传结果。
powershell -File tools/check_project.ps1
```

协议编码边界的实际实现：

```python
# 正确：每个字段独立验证，不能夹带第二条指令。
def wire_token(value) -> str:
    if type(value) not in (str, int, float):
        raise ValueError("协议字段只能是字符串或数值")
    text = str(value)
    if any(ch in ",;" or not 32 <= ord(ch) <= 126 for ch in text):
        raise ValueError("协议字段含分隔符、控制字符或非 ASCII 字符")
    return text
```

停止调用方取消的处理遵循 Python 的任务取消与屏蔽语义；保留任务引用，
`shield` 只保护内部停止事务，不把调用者取消伪装成停止成功。
见 [Python 3.12 asyncio 文档](https://docs.python.org/3.12/library/asyncio-task.html#shielding-from-cancellation)。
Host 限制与 Origin 检查是不同边界，见 [Starlette 官方 Host 防护说明](https://starlette.dev/middleware/#trustedhostmiddleware)。
旧 Vite 的 Windows 路径问题有条件限制，不能等同于本项目已经被利用；
见 [Vite 官方安全公告](https://github.com/vitejs/vite/security/advisories/GHSA-fx2h-pf6j-xcff)。

## 工程化边界与剩余任务

1. **停止不是硬实时抢占。** 命令通道仍串行等待在途回执；6.5 秒是软件事务等待上界，
   不是承诺机械臂会在该时间内物理停稳。原通道损坏时只报告停止未知并闭锁，
   不偷偷在新连接补发。软件 `GrpStop` 不替代现场物理急停。
2. **碰撞是有限离散检查。** 弦线采样与当前几何不能作为连续扫掠证明；
   真机 TCP、工具／负载、停止距离和现场风险评估本轮均未验证。
3. **本轮不是整库审查完成。** 登录／JWT／SQLAlchemy／SQLite 不是当前已实现模块，
   不按模板臆造问题；全页 a11y、各部署环境、所有导入导出、Python 依赖传递漏洞及
   后端生命周期／World 分层尚未完成穷尽审查，不能给整库健康度总分。
4. **旧测试未全量合并。** 历史 `tools/check_hand_follow.py` 的私有手臂／旧中心夹具此前有
   44 条失败断言，本轮没有通过放宽边界“修绿”，也不计入本轮通过数；后续需按现行共享机器人契约重整。
5. **真实浏览器／硬件未实测。** 前端是实际 SFC／状态机／模型的离线回归与生产构建，
   不冒充浏览器 GPU 帧率、实机轨迹一致或现场停机验收。
6. **远端 CI 没有启用。** 自动执行和检查结果上传涉及外部权限，审核拒绝了该扩展；
   当前只有本地验收入口。如需启用 GitHub 自动执行或上传结果，须用户另行授权。
7. **部署仍限定单控制进程。** 不应把带硬件控制权的 World 用多 worker／多副本启动；
   进程级控制权租约与独立控制服务是后续架构任务，不以文档代替已实现能力。

## 备份与数据保护

- 重构前源码／桌面工程／运行数据备份：`output/migration/pre-layout-20261027-123512.zip`。
- 被移出的桌面工程、旧包空壳和桌面专用工具保留在 `output/migration/`；不在活动导入路径。
- 移除根级 `app.py`、`run_panel.bat`、`run_sim.bat`；统一使用 `start.bat`。
- 迁移与回归完成前后，对 13 个真实运行数据文件逐个比较 SHA-256，一致；测试只在临时副本写入。
- 备份目录已忽略，不提交、不上传。未修改 `.env` 凭据。

## 最终验收

统一入口 `tools/check_project.ps1` 最终退出码 0，并输出 `PROJECT_CHECKS_OK`：

| 检查 | 最终结果 |
|---|---|
| 根目录结构与桌面解耦 | 7/7 |
| 前端实际脚本／SFC／模型回归 | 422/422 |
| 后端临时副本 | 20/20 组，合计 356 条测试或检查断言通过 |
| Vite 6.4.4 生产构建 | 124 模块构建通过 |
| npm 官方生产＋开发依赖审计 | 0 项已知漏洞；不代表 Python／全部供应链审计 |
| PowerShell 启动脚本解析 | 通过 |
| 运行数据 SHA-256 | 启动新服务前，13/13 与迁移前备份一致 |
| 新目录服务 | 首页、入口 JS、模型接口均 200；最新会话 `b5842c0ab8551a5b` |
| 新服务运行状态 | 仿真、未运动、两种跟随均关闭、真机未连接、相机未打开 |

最终后端证据：`output/checks/huayan-fix-checks-ct53l0mv.json`。
前端入口：`assets/index-DrdOZLz3.js`；原保存关节角未重置。
启动后的正常日志／姿态持久化写入不计作迁移数据损坏。

早期失败证据保留在 `output/checks/`，不删除失败记录。发现并处理了旧阈值／越限照发断言、
虚拟 HTTP Host 与可信 Host 不匹配，以及假控制器仅发单帧导致 ACK 后屏障没有持续反馈的问题。
测试改用正确的现行契约和持续周期假反馈，没有放宽生产安全阈值或到位屏障。

## 第二轮：全部剩余软件问题继续修复（2026-10-27）

本节取代前文有关“旧跟随测试未重整”“Python依赖未查询”“未做干净安装”的状态。
前文是第一轮历史记录，不删除当时的失败证据；本节也不等同于整库零缺陷或现场认证。

### 已修复的问题

| 优先级 | 精确位置 | 问题、修复和验证 |
|---|---|---|
| P0 | `backend/core/world.py:253` | 正常关闭可能只取消任务/断链，没有先统一停止；改为先调用停止事务，避免再次无限等待拒绝取消的旧作业；`check_service_lifecycle.py` |
| P0 | `backend/core/robot_real.py:258` | 关闭会清掉调用方短等待超时后仍排队的停止；先有界等待原会话已受理事务，再清链路；不新增连接或重放；`check_real_stop_transport.py` |
| P1 | `backend/core/service_lease.py:6`；`backend/main.py:134` | 仅文档要求单进程，实际可以重复启动控制者；增加操作系统排他锁、失败清理和配对生命周期；跨进程争用回归通过，不宣称跨主机租约 |
| P1 | `backend/main.py:1023` | 首帧发送在订阅清理保护区外，断线漏掉订阅；首帧进入try/finally，异常释放；`check_service_lifecycle.py` |
| P1 | `backend/schemas.py:6`；`backend/main.py:512` | 手拖请求把字符串false当开启；接线确认、点动和数值存在隐式类型转换；改为严格布尔/数值，错误包不进入控制方法；`check_api_control_inputs.py` |
| P1 | `backend/core/kinematics.py:307` | 无工具安装面没有visual网格，物体监测集合为空；补与四墙检测同坐标的安装面点，不虚构工具尺寸；`check_features.py` |
| P1 | `backend/core/world.py:585`；`backend/core/planning.py:110` | 圆柱显示用半径，部分碰撞路径却当直径，低估包络；物理、预检和运行共用圆柱包络定义；`check_object_inputs.py` |
| P1 | `backend/core/objects.py:126`；`backend/core/scenes.py:33` | 非有限几何被静默改成0、非法布尔可关碰撞、坏导入丢项/截断；拒绝整份非法变更，失败保留原场景；`check_object_inputs.py` |
| P1 | `backend/core/config.py:172`；`backend/core/program_store.py:33`；`backend/core/points.py:62`；`backend/core/calibration.py:72`；`backend/core/tools.py:114` | 部分存储先改内存再写盘，失败后出现两份真相；写成功才提交，返回副本并校验有限值；`check_core_storage_boundaries.py` |
| P1 | `backend/core/config.py:101`；`backend/core/eventlog.py:97` | 小数日志条数或坏历史围栏可导致启动失败；清日志与追加未用同一锁；校验并回退坏历史字段、不在读取时改盘，清空失败向上报告；`check_eventlog_settings.py` |
| P1 | `backend/core/ai_plan.py:50` | AI DO参数经int截断、布尔混入运动数值，引用点位未重验；拒绝小数DO/非有限值，复制并校验引用点；`check_core_storage_boundaries.py` |
| P1 | `backend/core/python_program.py:130` | 浅层长度限制挡不住共享嵌套数据的巨大原生展开；增加展开成本、深度、循环引用及原生操作前预算，容器修改预检后提交；`check_python_data_budget.py` |
| P1 | `backend/request_limits.py:7`；`backend/main.py:95` | 无实际请求体上限，URL令牌可能进入日志/历史；JSON解析前限制2MiB/接收10秒，令牌只走请求头，禁止URL凭据；`check_request_limits.py`、`check_access_freshness.py` |
| P1 | `web/src/store.js:107`；`web/src/components/CameraCloud.vue:40` | 旧启动/点云请求可覆盖新会话状态；增加代次校验、取消、旧结果忽略和空状态清理；`check-frontend-lifecycle.mjs` |
| P1 | `web/src/components/Spark.vue:76`；`web/src/workspaceState.js:26` | 图表漏首次/尺寸重绘和清理；异常数值导致toFixed崩溃；存储访问被禁可中断挂载；增加真实行为回归与失败返回 |
| P1 | `backend/requirements.txt:9` | 已安装certifi 2026.7.22受公开证书信任漏洞影响；升级并固定2026.10.19，复查全部已安装包；没有升级系统Python或卸载原环境PyQt |
| P2 | `tools/offline_checks.json:1`；`tools/check_hand_follow.py:1`；`tools/check_hand_follow_core.py:1` | 旧独立手臂/瞬移/丢掌停用契约不适用于共享机器人；保留原覆盖并改测真实契约，主测试34项、纯类31项；所有软件回归登记，真实相机脚本和越界路径拒绝进入离线运行器 |

已按“复现→定位→最小修复→回归”执行，三个协作分工分别处理前端、存储/解释器、旧跟随夹具；
没有为测试变绿降低生产碰撞阈值、停止闭锁或到位屏障。

正常关闭和生命周期处理参考 [FastAPI官方生命周期说明](https://fastapi.tiangolo.com/advanced/events/)。
证书问题及影响条件见 [certifi官方公告](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2)，
修复版本的 [PyPI元数据](https://pypi.org/pypi/certifi/2026.10.19/json) 不再报告该漏洞。

### 最终验证与记录

- 结构检查9/9；前端446/446，未跳过；124模块生产构建通过。
- 后端47组均有通过证据。完整运行先45/47通过，之后修复两处测试夹具并重跑失败组；**不能把首次完整运行写成全绿**。
- 首次最终范围运行：`output/checks/huayan-fix-checks-mm8ajfqf.json`。
  AST测试补齐真实依赖后及相邻碰撞链：`output/checks/huayan-fix-checks-gos3753b.json`；
  冒烟到位等待按真实exec字段和完成状态修正后：`output/checks/huayan-fix-checks-kblhesu8.json`。
  三份记录按每组最新结果合并，47组、0失败。生产后端源码逐个SHA-256与完整运行副本一致。
- 最后冒烟等待原本8秒后静默返回，即使仍在预检/运动也继续下一步；现为20秒有界状态等待、超时明确失败。
  没有给生产机器人放宽等待或安全阈值。中间字段名修正失败记录同样保留。
- 原环境30个Python包官方PyPI查询全部成功：升级前仅certifi受影响，升级后0包有已知漏洞。
  记录：`python-dependency-audit.json`、`python-dependency-audit-fixed.json`。查询经过用户明确允许，只发送包名/版本。
- 新建临时Windows Python3.12环境，仅安装官方二进制runtime依赖，无PyQt/相机SDK；`pip check`通过。
  安装记录 `python-clean-install.json`，初次核心回归 `huayan-fix-checks-xnxzbnsa.json`，最终新增边界复验 `huayan-fix-checks-7otrdg4h.json`，均位于`output/checks/`。
  修复了旧pip按GBK读取UTF-8依赖注释导致干净安装失败的问题。
- 用户授权关闭当时正在采集的摄像头并更新；已通过归属校验脚本重启本地服务。
  当前会话`0e465b59da1843aa`，入口`assets/index-D2b3sUKZ.js`；仿真、无运动、两个跟随关闭、真机未连接、相机关闭。
  六轴仍为`[-0.014,73.407,101.241,180.017,117.834,0.009]`，没有用重置姿态冒充更新成功。
- 旧HANDOFF含“越限照发”等废止指令，已保留到`output/migration/HANDOFF-pre-second-pass-20261027.md`，活动交接改为现行约束。

### 未完成的验收与架构边界

1. 真机停止距离、现场安全回路、TCP/工具/负载和实机轨迹一致性没有现场验证；未连接真机，Servo未开放。
2. Tabbit曾成功只读观察更新前页面，但随后连接中断、页面清单为空，不能完成更新后刷新/视角保持、窄屏或GPU性能验收。
   已结束空任务，没有关闭用户页面、切换其他浏览器或伪造截图。代码行为回归不替代这项实测。
3. World/HTTP仍是大型单体并存在导入初始化；同数据目录锁不是跨主机控制权租约。
   合作式解释器的预算不是进程/操作系统强隔离，离散碰撞也不是连续扫掠证明。
4. 干净安装只覆盖本机Windows Python3.12的基础依赖，不代表Linux、其他Python、相机驱动/SDK或全部部署环境验证。
5. 没有启用远端CI、提交、推送、上传日志或改写`.env`；不能宣称企业级现场交付全部完成或整库不存在未知问题。
