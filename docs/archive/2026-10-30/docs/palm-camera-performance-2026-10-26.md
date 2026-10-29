# 掌心/相机性能与刷新续接接口专项（2026-10-26）

## 边界和基线

本轮只修改 CameraFrame.vue、handFollowInput.js、public/vision/hand-worker.js、backend/core/cameras.py 及 check-palm-camera.mjs、check_camera_frames.py。没有操作真实设备、扫描/调整相机、浏览器、服务、build，也没有修改 App/store/scene/HandFollowPanel。没有新增依赖。

主代理提供的既有实测（不是本轮重新测量）：采集约15fps，CPU检测约6–9fps，GPU重复同一JPEG推理约95ms；此前采集 read 约59ms、encode约5ms。重复JPEG推理时间不是持续检测帧率，也不能证明GPU优于CPU。采集与推理均限制吞吐，仅优化前端无法宣称30fps。

## 定位与实现

1. **分片解析的重复复制可离线复现**。原解析器每收到分片就拼接旧缓冲，JPEG完成时再 slice 一次。现逐字节扫描有界头部，只保留JPEG分片，完成时构造不可变Blob；重复/旧seq不构造JPEG Blob。不再显式复制增长中的JPEG缓冲。Content-Length上限8MiB、头部上限8192字节、逐帧seq/stamp和二进制完整性检查保留。
2. **检测解码移到工作线程**。worker ready通过 `frameInput:'jpeg'` 宣告能力，CameraFrame发送同一不可变JPEG Blob和原MediaPipe时钟；工作线程解码、等比例缩到原有640px长边上限、推理后关闭bitmap。缺少worker createImageBitmap时保留原主线程bitmap兼容路径。预览仍有独立解码，本轮没有宣称消除所有双解码，也没有承诺浏览器内部零拷贝。
3. **增加可核验耗时**。本地输入总线增加 `decodeMs`（worker解码）、`roundTripMs`（提交到结果回调）、`inferenceSize`。原 `inferenceMs` 仍只计detectForVideo。旧bitmap兼容路径的decodeMs不是主线程解码时间；不应混用解释。已有采集read/encode统计不变。
4. **不以压低识别质量换数字**。本轮没有默认降低到480/384，也没有引入未经实际识别率对照的自适应降分辨率。MediaPipe内部已有固定网络输入处理；更小源图能否明显减少95ms推理、以及小手掌是否漏检，均需主代理用真实不同距离掌心对照后决定。
5. **时间戳纠正**。相机JPEG缓存时间戳改为host read/poll返回时记录，不再编码结束后重新打时间戳，保证后续图像处理/编码延迟计入观察年龄。它不是硬件曝光时间，无法消除驱动内部缓冲未知延迟；没有暗改曝光、FPS、分辨率、驱动参数。
6. **安全/坐标不变**。每次只允许一个检测请求，忙时替换最新待处理帧而非排队；拒绝旧seq、跨session迟到结果。21点、骨架、镜像一次、letterbox映射、漏检显示防抖、真实timestamp和300ms运动输入过期均保留。显示保持不会刷新运动凭据。

## 离线基准与测试

执行 `node --test sim/web/scripts/check-palm-camera.mjs`：45/45通过。使用实际Vue脚本及实际worker在可控假环境执行，不加载浏览器/模型/相机。

解析微基准固定20张65536字节JPEG，每段1024字节，比较原拼接算法与当前解析器：

| 指标 | 原算法 | 当前算法 |
| --- | --- | --- |
| 完整输出帧 | 20 | 20 |
| JS Uint8Array.set复制的字节总数 | 43,781,245 | 0 |
| 单次运行耗时，首次观测 | 26.64ms | 4.28ms |
| 单次运行耗时，再次观测 | 49.18ms | 6.50ms |

复制计数不含Blob内部存储、header逐字节写入、解码、推理、GPU/网络成本；耗时受机器负载/JIT影响，仅为非门禁观测。不能把这个比值当作整页、模型或真实设备加速比。

兼容回归 `node --test sim/web/scripts/check-hand-follow-client.mjs`：54/54通过，仅运行现有文件，未修改该文件。

后端使用项目 `.venv/Scripts/python.exe tools/run_offline_checks.py check_camera_frames.py` 在临时源码副本执行，禁止外部连接，禁用真实机器人；新增慢编码400ms仍保留read返回时间戳测试。最终 **30/30通过**，测试用时1.170s，报告 `output/checks/huayan-fix-checks-5nezqj3t.json`。首次受限环境运行在27个同步用例后，异步接口测试超时180s（`huayan-fix-checks-qsj764xe.json`）；相同代码在允许本地测试异步套接字的环境重跑通过。保留该失败，不将首次超时算作通过。既有FastAPI on_event弃用警告未在本次范围外顺带修改。

## 提供给主代理的刷新接口（尚非页面接入验收）

`createHandFollowClient` 新增：

- `getState()`/onChange快照包含本地最后发出 `seq`。
- `resumeOwned(saved, live, backendSession): boolean`：纯本地续接，不发configure/start。saved应为同一tab的 `getState()` 加 `backendSession`，仅由sessionStorage保存。要求saved与live均active/configured，backendSession相同、原token和camera session相同、guard('sample')无错误，本client非busy/active/已dispose。seq须安全整数，saved.seq≥0，saved.server.seq≥-1且≤saved.seq，live.seq≥saved.server.seq，最大值必须还能安全递增。允许saved.seq领先live（发送未确认）或live领先saved（旧页请求稍后被确认），下一次发送 `max(saved.seq,live.seq)+1`。
- `detach()`：永久停用本地client，清理命令/采样定时器并abort本地采样，不发stop，迟到回调不重新接管/停止服务端。旧client不能重用。
- 原 `dispose()` 仍明确停止原拥有会话；detach后的dispose不再发送stop。普通stop仍有效。

拒绝测试覆盖无凭据/陌生token/相机会话变化/后端变化/不合法或倒退seq/服务端非active/configured/guard拒绝/busy/重复续接；正例验证不发start且采样严格递增。detach覆盖在途abort、迟到采样/配置回包、永久停用。

这里没有写入sessionStorage，也没有接入HandPanel pagehide。主代理须接入并验证：刷新而非新tab恢复、后端重启拒绝、手动Stop清凭据、pagehide/unmount顺序、BFCache返回处理、过期watchdog保持安全；不能仅凭接口测试宣称整个页面已实现刷新保持。

pagehide保存时应直接取 `client.getState()`，不要只保存上一次onChange缓存：发送尚未收到回包的sample已消耗seq，但还未触发结果onChange。detach应在保存之后调用。seq只用于单调性检查，不替代token/后端会话验证。

## 待主代理最终核验

- 实际浏览器worker是否选用JPEG输入，GPU是否仍可用/CPU降级，冷启动与持续运行耗时。
- 对比采集/检测FPS、decode/inference/roundTrip和观测年龄分布、页面长任务；不是只读requested/driver-reported FPS。
- 静止/移动、小/大掌心、短暂漏检、遮挡、切换相机、隐藏/恢复页面、300ms超时与丢掌回中心；21点与镜像角点一致。
- 未证实30fps或相机硬件上限；不据此承诺整页丝滑或机器人实机运动一致。
