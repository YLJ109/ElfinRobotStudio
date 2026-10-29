# 新Agent／新对话上手

## 先读短入口

[根AGENTS](../AGENTS.md)→[HANDOFF](../HANDOFF.md)→[STATUS](STATUS.md)→[TODO](TODO.md)。先核对用户最新目标，不从旧“下一步”恢复被取消任务。
需要全局规则时按G:/AIMem/统一入口读取；无法访问明确报告。本项目不维护第二份全局画像、工具清单或私有记忆。

## 再按问题定位

| 问题 | 首查 |
| --- | --- |
| 现在能做什么/不能做什么 | USER_GUIDE、ROBOT_OPERATIONS |
| 代码在哪里 | PROJECT源码地图 |
| 全面翻新如何进行 | PLAN、ARCHITECTURE_RENEWAL |
| 残影/笛卡尔不一致 | CART专项→targetState/PointsPanel/world/planning/kinematics |
| 真机跟随被挡 | REAL专项→real_follow_runtime/qualification，核对blockers |
| AI是真的调用还是本地规则 | AI专项→ai_plan/ai_intent/aiConversation |
| 某按钮死/重复 | TRUTH专项→页面→store→route→实际消费者→终态 |
| 历史证据在哪里 | archive/MANIFEST.json，以原路径locate |

```powershell
node tools/docs.mjs find "真机 停止"
node tools/docs.mjs find "名称 草稿"
node tools/docs.mjs locate docs/repair-report-2026-10-26.md
```

不要开场读取整个project.json、整份详细审查和全部归档。先查命中ID/章节，必要时打开源码；日期新不等于事实正确。

## 必须记住的项目纠正

- 跟随已生产接入，不是仅staged；现场可用尚未证明。
- 点位能到不等于MOVEL/Servo都合格，残影是终点不是路径。
- J6=0用于特定跟随，不强加于所有笛卡尔任务。
- 八项吸盘操作不是八路DO；关气不一定安全。
- YOLO与广格式导入仍计划；现有识别是颜色轮廓。
- 规则整理、本地诊断、模型回答分清；模型输出不是命令许可。
- 旧README/交接含互相冲突的历史阶段，已归档，不作为默认上下文。

## 每次交接最小内容

当前用户目标/授权边界；改过的文件；真实完成与证据；未运行/未验；剩余ID；下一条可做动作；阻碍及回退。不要保存内部思维过程、凭据或聊天全文。
完成项从TODO移出但保留对应证据/版本；未确认状态不写done。更新计划源并同步相关文档；全局记忆变更只在原库按权限处理，本轮未写全局库。
