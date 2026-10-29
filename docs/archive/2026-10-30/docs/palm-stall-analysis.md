# 掌心漏检性能专项（2026-10-27）

本轮修复了离线可复现的无效推理和重复界面更新；尚未现场确认“每次漏检导致整机卡顿”的主因。没有打开相机、操作机器人、启动/重启服务或执行 build。没有修改 scene.js、HandFollowPanel.vue、后端、package.json 或既有测试。指定调试与 three-webgl 技能用于先复现、区分工作量与真实帧时、按同样本复测。

## 证据与改动

- `CameraFrame.vue` 原先在接收捕获帧和独立预览图片 load 时都发布相同输入。订阅方会重复运行验证/响应式更新。现在同一捕获帧和相同尺寸的 load 不再重复发布；初次获得尺寸、分辨率变化、检测结果、过期定时器和流新鲜度通知仍立即生效。
- `createPalmTracker.read()` 在 lost 状态每次创建新空数组，使骨架计算持续失效。现在复用不可变空数组；真实21点更新仍正常触发绘制。另修复平滑分支丢失 z 的问题，保留有限的测量 z 并沿原时间平滑；`hand_open` 计算和传递保留。
- 已超过动作有效期的 JPEG 原先仍进入 worker 解码和 `detectForVideo`，然后才被 tracker 丢弃。现在随帧发送原始捕获 epoch 时间戳加300ms的截止时间；worker 在解码前、解码后分别检查。过期即回送匹配 frameSeq 的 `skipped` 确认，释放单槽并处理最新帧，不进入昂贵推理、不增加检测FPS。bitmap 在 finally 关闭。不同线程的 performance.now() 起点没有混用。
- 原有 worker JPEG 解码、GPU优先/CPU回退、640px上限都是前序实现，本轮没有把它们算作新增优化。没有改阈值或降分辨率。漏检路径未发现主动重建检测器；新增测试覆盖连续空结果及重新捕获仅初始化一次。
- 状态改成“短暂光标保持 · 等待新检测”，只描述画面光标，不承诺机器人保持位置。原动作年龄300ms、显示450/650ms边界不变；显示保持不续期动作凭据。真实21点、镜像一次、原 frameSeq/timestamp 和最新帧单槽均保留。已开始的同步推理不能被中断；若完成时过期，原 tracker 仍拒绝其动作输入。

## 同样本前后对比

执行 `node sim/web/scripts/check-palm-stall.mjs --profile`。Node v24.22.0；执行实际编译后的 Vue 脚本和实际 classic worker 源码，使用受控时钟、假 JPEG/检测器，不加载浏览器、模型或GPU。表中是实际发生的调用次数，不是预测的真实检测性能。

| 固定样本与指标 | 修改前 | 修改后 |
| --- | ---: | ---: |
| 180次捕获、60次完成（21次空结果）、30次 housekeeping：发布次数 | 452 | 272 |
| 同样本：骨架计算次数 | 154 | 42 |
| 同样本：提交帧 / worker创建 / worker终止 | 61 / 1 / 0 | 61 / 1 / 0 |
| 60张提交时已过期50ms的帧：解码 / 推理 | 60 / 60 | 0 / 0 |
| 60张剩余50ms、模拟解码70ms的帧：解码 / 推理 / 关闭bitmap | 60 / 60 / 60 | 60 / 0 / 60 |

worker样本中的7ms解码、95ms推理（另一组70ms解码）仅推进受控时钟，绝非实测模型时延。CPU harness 热身3轮、采样10轮：修改前中位26.15ms、p95 39.42ms；最终修改后中位24.58ms、p95 30.17ms。另一次修改后为16.48/19.12ms，说明机器并行负载/JIT噪声明显，不能由这些数值声称整页或整机加速比例。调用次数在复测中一致。

## 回归与限制

- 修改前原 npm test：372/372通过。
- 最终新增专项：17/17通过，覆盖过期前/后解码、匹配确认、错误seq/会话、单槽恢复、不虚增FPS、300ms动作拒绝、显示到期、21点/z/hand_open/一次镜像、预览尺寸通知和模板内存编译。
- 最终运行原 package test 中全部脚本加新增脚本：389项，388通过、1失败。失败为 `check-points-preview.mjs:287` 的 `cancel and sync actual stop repetition without sending a motion stop`，第291行找不到“同步回实际”按钮（`element for click exists`）。单独重跑该文件为36/37通过；当前 PointsPanel.vue 中没有这个按钮。该文件/测试未在本轮编辑，留主任务协调。
- 掌心相机与手势现有回归及新增专项首次合跑60/60通过；新增补充5项后专项17/17通过。`git diff --check` 对授权跟踪文件通过。既有测试包含Vue脚本/模板/样式内存编译，未执行构建。

没有真实浏览器长任务、worker持续跟踪/丢掌/重新捕获阶段耗时或GPU时间线，本轮不能确认 MediaPipe 重新定位、显卡争用、布局还是其它进程造成整机停顿。截图中手腕被裁切也未作为已验证根因。没有据此降低识别质量、修改场景或调整物理/仿真运动。

主任务提供并已核实的背景资料：[MediaPipe Web（同步检测与worker建议）](https://developers.google.com/mediapipe/solutions/vision/hand_landmarker/web_js)、[Hand Landmarker（跟踪失败会触发掌心检测）](https://developers.google.cn/edge/mediapipe/solutions/vision/hand_landmarker/index)。这支持后续现场分阶段测量方向，不证明本机瓶颈。浏览器验证若继续，只通过授权的 Tabbit 流程采集同设备、同视口、同动作序列的长任务、decode/inference/roundTrip与观测年龄，当前未执行。
