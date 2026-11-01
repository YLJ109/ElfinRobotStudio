# 玖枢 Code Wiki

本目录是 **面向开发者的代码地图**：解释当前仓库如何组织、各模块负责什么、关键类与函数在哪里、依赖如何流动，以及怎样把项目跑起来。

- 整理日期：2026-11-01
- 对应源码：当前工作区（项目目录名保留 `ElfinRobotStudio`，产品名 `玖枢 / JiuShuRobotStudio`）
- 事实优先级：**代码与绑定版本的证据 > 本 Wiki > 历史文档**。当前做到哪一步、哪些已验收，仍以 [当前状态](../STATUS.md) 和 [活跃待办](../TODO.md) 为准。
- 本 Wiki 不授予任何真机、相机、IO、外部模型或发布权限；也不会把"接口存在"写成"现场已验证"。

## 文档导航

| 文档 | 回答的问题 | 适合谁 |
| --- | --- | --- |
| [01 总体架构](01-architecture.md) | 系统由哪些进程/层组成，状态和指令如何流动，哪些不变量不能破 | 所有人，先读这篇 |
| [02 后端](02-backend.md) | FastAPI 入口、World 状态机、运动学/规划/安全、程序、视觉、真机链路的关键类与函数 | 后端/算法开发者 |
| [03 前端](03-frontend.md) | Vue 应用、导航、Pinia store、Three.js 场景、任务控制器、面板与组件 | 前端开发者 |
| [04 桌面端与构建](04-desktop.md) | Electron 主进程、私有后端、IPC、PyInstaller 与安装包 | 桌面/发布维护者 |
| [05 API、数据与安全边界](05-api-and-data.md) | REST/WS 协议、数据文件格式、访问控制、远程相机契约、备份恢复 | 联调/运维/安全审查 |
| [06 依赖与运行手册](06-dependencies-runbook.md) | 版本矩阵、依赖图、启动/构建/测试命令、环境变量、常见故障 | 新成员/排障 |
| [07 模块索引](07-module-index.md) | 每个后端/前端/桌面文件的单行职责，按目录速查 | 需要快速定位的人 |

## 三条最常用的路径

**第一次跑起来（离线、禁止真机）**

```powershell
powershell -NoProfile -File tools/env_doctor.ps1
$env:HUYAN_NO_REAL = '1'
.\start.bat dev
# 浏览器打开 http://localhost:5173/
```

**只想读代码，不启动服务**

- 后端从 [backend/main.py](../../backend/main.py#L82) 的 `create_app()` 进入；
- 前端从 [web/src/main.js](../../web/src/main.js) 和 [web/src/App.vue](../../web/src/App.vue) 进入；
- 桌面从 [desktop/main.cjs](../../desktop/main.cjs) 进入。

**改一个行为前先确认边界**

- 运动只有一个所有者：`World` + `MotionExecutor`；不要再起第二个后台运动通道。
- 预览不是命令，ACK 不是到位，停止请求不是已停稳，未知反馈不补零。
- 仿真通过不等于真机资格；真机相关改动必须单独验证。

## 阅读约定

- 文中的 `文件#L行号` 链接指向当前工作区源码；代码演进后行号可能偏移，以文件内容为准。
- "当前实现"描述代码里真实存在的路径；"候选/规划"不会出现在实现章节，除非明确标注。
- 涉及安全的结论只说明代码如何做，不替代现场风险评估。
- 文档链接由 `node tools/docs.mjs check` 校验；新增/改名文件后请同步 [文档索引](../README.md) 与 [SEARCH_INDEX](../SEARCH_INDEX.json)。

## 与既有文档的关系

| 既有文档 | 与本 Wiki 的分工 |
| --- | --- |
| [STATUS](../STATUS.md) | 当前进度、已验/未验、失败证据；Wiki 不复制验收结论 |
| [PROJECT](../PROJECT.md) | 简版源码地图与数据边界；Wiki 是其展开版 |
| [QUICKSTART](../QUICKSTART.md) | 上手步骤与入口差异；Wiki 补充依赖和服务内部结构 |
| [DEVELOPMENT](../DEVELOPMENT.md) | 开发/评审/证据分级规则 |
| [ARCHITECTURE_RENEWAL](../ARCHITECTURE_RENEWAL.md) | 目标架构与迁移方案，**不是已落地结构** |
| [ROBOT_OPERATIONS](../ROBOT_OPERATIONS.md) | 真机可做/不可做与现场禁止事项 |

> 一句话：**要判断"现在能不能用"，看 STATUS；要判断"代码在哪里、怎么接"，看本 Wiki。**
