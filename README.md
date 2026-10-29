# 玖枢 · JiuShuRobotStudio

本地六轴机器人工作站，包含仿真、示教、程序、三维场景、视觉和受限真机接入。项目目录保留 ElfinRobotStudio 名称。当前不是已完成现场认证的工业控制产品。

## 从这里开始

| 你要做什么 | 入口 |
| --- | --- |
| 第一次了解项目 | [项目文档](docs/PROJECT.md) |
| 安装与离线上手 | [上手文档](docs/QUICKSTART.md) |
| 使用现有功能 | [用户指南](docs/USER_GUIDE.md) |
| 真机、吸盘、停止和禁止事项 | [机器人操作指南](docs/ROBOT_OPERATIONS.md) |
| 当前做到哪里 | [当前状态](docs/STATUS.md) |
| 本期方案、全面翻新 | [方案总览](docs/PLAN.md) |
| 接下来做什么 | [活跃待办](docs/TODO.md) |
| 新 Agent／新对话接手 | [短交接](HANDOFF.md) → [接手指南](docs/AGENT_ONBOARDING.md) |
| 全部文档与检索 | [文档索引](docs/README.md) |

## 必须知道

- 真机通信与跟随源码存在，不代表当前设备可通或已现场可用；工具、控制器资格和停止验证仍需逐项核对。
- 笛卡尔输入目前生成关节目标；真机默认逐轴兼容运动，不保证TCP直线。
- 左右吸盘八项操作、任意3D文件导入、YOLO和新玩法的增补仍属规划，不能按计划文字当现成功能。
- 当前已知缺陷和正式检查入口问题见[状态](docs/STATUS.md)，不能宣称全项目检查已绿。
- 开发启动可能准备依赖和加载用户保存的自动连接偏好。只做离线演练时先采用上手文档的进程级禁真机方式。
- 本轮文档治理没有启动应用、操作设备、调用模型、构建或打包。

## 本地文档工具

```powershell
node tools/docs.mjs find "真机 停止"
node tools/docs.mjs check
```

该工具只读文件，不联网、不导入后端、不启动服务。检索默认排除历史归档；[治理说明](docs/DOCS_GOVERNANCE.md)解释单一事实源和恢复规则。

技术栈为 FastAPI、Vue、Three.js、Electron；正式代码仍位于 backend/、web/、desktop/。项目尚未指定整体开源许可证，公开或闭源发布前需审核协议、模型和第三方许可。

