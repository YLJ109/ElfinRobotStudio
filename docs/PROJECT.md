# 项目文档

## 定位与边界

玖枢是本地六轴机器人工作站，仿真、三维、示教、程序、视觉和受约束真机接入共享界面。不是任意机器人通用驱动，也不是已现场认证的安全系统。
现有栈保留FastAPI/Python、Vue/Pinia、Three.js、Electron；目标结构见[翻新方案](ARCHITECTURE_RENEWAL.md)，下表才是当前结构。

## 当前源码地图

| 领域 | 首查 |
| --- | --- |
| 应用/路由/共享编排 | ../backend/main.py；../backend/core/world.py |
| 运动学/路径/执行 | ../backend/core/kinematics.py、planning.py、trajectory.py、motion.py |
| 真机事务 | ../backend/core/robot_real.py、real_follow_link.py、real_servo_transport.py |
| 跟随与资格 | ../backend/core/real_follow_runtime.py、real_follow_host.py、real_follow_startup.py、real_palm_pipeline.py、real_follow_qualification.py |
| 工具/场景 | ../backend/core/tools.py、sim_tool.py、objects.py；../backend/robot_play_routes.py |
| 视觉/相机 | ../backend/core/cameras.py、vision_workspace.py；../web/src/cameraSource.js、virtualCamera.js |
| AI/程序 | ../backend/core/ai_plan.py、ai_intent.py、program.py、python_program.py |
| 页面/导航 | ../web/src/App.vue；panels/WorkPanel.vue、SystemPanel.vue、FunctionsPanel.vue |
| 客户端状态 | ../web/src/store.js、targetState.js、handFollowInput.js、aiConversation.js |
| 三维与资产 | ../web/src/scene.js、toolVisual.js；../web/public/ |
| 桌面与启动 | ../desktop/main.cjs、backend.cjs；../start.bat |
| 检查工具 | ../tools/offline_checks.json、run_offline_checks.py、check_project.ps1 |

表中同一单元格未写完整路径的文件位于该单元格首个目录；检索时用文件名，不把拟议目录当现有目录。

## 关键契约

- 预览不是命令；ACK不是到位；软件停止请求不是已停稳。
- 位置mm、姿态角度，参考系与TCP显式绑定；固定参考偏移只应用一次，不能凭照片删常量。
- 仿真结果、模型FK、控制器PCS、相机观测分别标来源与时效。
- 控制权唯一；模式/连接/工具/运动相关修订变化使计划失效，不重放旧操作。
- 稳定ID绑定物体/点位/任务，显示名可变；名称草稿和实时值分离是待完善内容。
- 一个最新帧槽不积压；可视框暂留不更新原始采集时间。

## 数据位置

源码默认backend/data；ELFIN_DATA_DIR可覆盖，见../backend/paths.py。桌面自建后端可能使用Electron userData/data；源码桌面也可能复用已有服务。不同入口不保证共享同一数据目录。
模型优先web/public，前端发布产物web/dist。output/checks保存检查结果，不能用“构建垃圾”名义清理验收证据。

## 接口与身份

REST/WS入口由backend/main.py及各routes模块声明；默认后端8710，Vite5173代理8710。真机当前设计10003命令与10004状态，不适用于其他品牌协议。
接口语法存在不等于设备当前支持；未资格动作保持后端拒绝。API/工具/资产指纹与版本清单属后续发布治理，不宣称现已完善。

## 配置与许可

凭据仅用环境/密钥机制，不写文档、前端构建或日志；不展示.env值。整体许可证尚未选定，资源/YOLO等授权需分别核验。默认首次不自动连接不代表用户保存过的偏好一定关闭。
