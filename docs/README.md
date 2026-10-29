# 文档索引

更新：2026-10-30。先按读者取必要文档，不要求整库加载。

| 文档 | 负责回答 | 不负责 |
| --- | --- | --- |
| [STATUS](STATUS.md) | 当前源码、已知问题、证据限制 | 将历史成绩当当前认证 |
| [PROJECT](PROJECT.md) | 当前结构、源码地图、接口与数据边界 | 虚构重构已落地 |
| [QUICKSTART](QUICKSTART.md) | 环境、离线开发、启动入口差异 | 自动执行命令或现场放行 |
| [USER_GUIDE](USER_GUIDE.md) | 现有功能使用与恢复 | 把路线图写成已上线 |
| [ROBOT_OPERATIONS](ROBOT_OPERATIONS.md) | 真机、IO、运动、停止的可做/不可做 | 替代厂家手册或现场风险评估 |
| [PLAN](PLAN.md) | 本期范围、依赖、验收与非目标 | 复制全部缺陷详情 |
| [ARCHITECTURE_RENEWAL](ARCHITECTURE_RENEWAL.md) | 全面翻新、目标目录、迁移与开源对标 | 本轮直接搬业务代码 |
| [TODO](TODO.md) | 尚未完成的工作包与入口 | 积累完成项和聊天流水 |
| [DEVELOPMENT](DEVELOPMENT.md) | 开发、测试、评审、分支与回退 | 默认允许硬件测试 |
| [OPERATIONS](OPERATIONS.md) | 数据备份、升级、事故和发布 | 自动恢复旧运动 |
| [AGENT_ONBOARDING](AGENT_ONBOARDING.md) | 新Agent/对话快速接手 | 全局记忆副本 |
| [DOCS_GOVERNANCE](DOCS_GOVERNANCE.md) | 文档来源、检索、归档和维护 | 自动认证内容正确 |
| [详细审查方案](comprehensive-optimization-plan-2026-10-29.md) | FIX/ENH/NEW/APP/CART/TRUTH/REAL/AIQ的细节 | 开场必读 |
| [真机验收模板](real-hardware-acceptance.md) | 已有人工验收记录与证据边界 | 已完成现场验证 |
| [真机接入历史](real-follow-implementation-2026-10-29.md) | 顶部生产接入记录及历史阶段 | 当前运行服务证明 |
| [UI风格约定](ui-style-contract-2026-10-27.md) | 既有青色主题及控件风格 | 无障碍/视觉认证 |

## 单一来源

- 需求、优先级和方案：project.json 的活跃节。
- 当前事实：STATUS摘要，来源是代码及原始证据；需要时回查，不凭修改日期判定。
- 活跃工作：TODO只保留未完成包；详细验收不复制多份。
- 历史：archive/MANIFEST.json。文档清理不改写旧证据，不按旧文档默认恢复任务。

## 检索

```powershell
node tools/docs.mjs find "吸盘 IO"
node tools/docs.mjs find "笛卡尔 残影"
node tools/docs.mjs find "Agent 接手"
node tools/docs.mjs locate docs/repair-report-2026-10-26.md
node tools/docs.mjs find "旧兼容跟随" --history
node tools/docs.mjs check
```

默认检索活跃MD；历史显式开启。检索只是本地字词定位，不是语义RAG或事实认证。完整机制见[治理说明](DOCS_GOVERNANCE.md)。
