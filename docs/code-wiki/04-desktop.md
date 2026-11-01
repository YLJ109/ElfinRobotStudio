# 04 · 桌面端与构建

桌面端是 Electron 应用，负责窗口、进程、权限和本地后端编排；业务代码仍在 `backend/` 与 `web/`。

- 主进程：[desktop/main.cjs](../../desktop/main.cjs)
- 后端会话：[desktop/backend.cjs](../../desktop/backend.cjs)
- 私有后端入口：[desktop/backend_entry.py](../../desktop/backend_entry.py)
- 打包：[tools/build_desktop.py](../../tools/build_desktop.py)

## 1. 进程结构

```text
electron main (desktop/main.cjs)
  ├─ splash 窗口（启动进度）
  ├─ mainWindow（工作站页面）
  ├─ visionWindow（独立视觉工作站 /vision.html）
  └─ BackendSession
       ├─ 源码态：backend/.venv/Scripts/python.exe -B desktop/backend_entry.py
       └─ 打包态：resources/backend/ElfinBackend.exe
            ├─ FastAPI（127.0.0.1:0 随机端口）
            ├─ 挂载 web/dist 页面
            ├─ 可选：resources/vision/ElfinVision.exe（MediaPipe worker）
            └─ 数据目录：Electron userData/data 或恢复副本
```

### 1.1 主进程

[main.cjs](../../desktop/main.cjs)：

- `app.setName('JiuShuRobotStudio')`、单实例锁、`--quit` 处理；
- 创建无边框窗口：`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`；
- `protect(window)`（[L49](../../desktop/main.cjs#L49)）：拒绝新窗口、拒绝非本站导航、拒绝 webview；
- `senderWindow(event)`（[L29](../../desktop/main.cjs#L29)）：IPC 只接受主窗口/视觉窗口/启动窗口的**主 frame 且 URL 匹配**；
- `start()`（[L114](../../desktop/main.cjs#L114)）：清缓存 → 启动后端 → 创建窗口 → 加载 `base + '/#/sim'`；
- `closeApplication()`（[L91](../../desktop/main.cjs#L91)）：先 `backend.close()`（受认证停止），失败则保留窗口并提示；
- `openVisionWorkspace()`（[L72](../../desktop/main.cjs#L72)）：独立视觉窗口；
- 权限：`createCameraPermission()` 只允许自有窗口的主 frame 申请 `video` 媒体权限；
- CSP：主 frame 注入 `default-src 'self'` 等限制（[L160-L168](../../desktop/main.cjs#L160-L168)）。

### 1.2 IPC 面

[preload.cjs](../../desktop/preload.cjs) 只暴露：

| API | 作用 |
| --- | --- |
| `minimize/toggleMaximize/close/getWindowState` | 窗口控制 |
| `openVisionWorkspace` | 打开独立视觉窗口 |
| `recoveryState/useRecovery` | 数据恢复副本切换 |
| `onWindowState` | 窗口状态订阅 |
| `reportBoot` | 启动进度上报 |
| `onBootProgress` | 启动进度订阅 |
| `retry` | 启动失败重试 |

主进程 handler（[L169-L195](../../desktop/main.cjs#L169-L195)）逐项校验来源，非主窗口不能打开视觉窗口或切数据目录。

### 1.3 后端会话

[backend.cjs](../../desktop/backend.cjs#L18)：

- 源码态优先探测 `127.0.0.1:8710`：只有 `/api/state` 合法、`/api/build` 身份兼容、且 `servesWindow()` 能返回带 `id="app"` 的页面才复用；
- 否则拉起私有后端；私有后端用随机端口，通过 stdout 的 `{"type":"desktop-listening","port":...}` 协议上报；
- 用 `ELFIN_DESKTOP_KEY` 生成 32 字节会话密钥；`/api/desktop/status` 与 `/api/desktop/shutdown` 必须带 `x-elfin-desktop-key`；
- `waitReady()`（[L89](../../desktop/backend.cjs#L89)）30 秒内轮询状态，返回 `ready && pid` 才算成功；
- `close()`（[L108](../../desktop/backend.cjs#L108)）先走受认证停止，要求 `settled !== false`，再等子进程退出；
- 复用外部 8710 时，关闭只发 `/api/points/stop`，不结束非本窗口启动的进程。

### 1.4 私有后端

[backend_entry.py](../../desktop/backend_entry.py)：

- 要求 `ELFIN_DESKTOP_KEY` 长度 ≥32；
- Uvicorn 监听 `127.0.0.1:0`，`access_log=False`；
- 注册 `/api/desktop/status`、`/api/desktop/shutdown`，并把它们排到静态兜底路由之前；
- 若 `web/dist/index.html` 存在，移除 `api_only_root` 后挂载 `StaticFiles`；桌面窗口因此能加载页面；
- 打包态自动设置 `ELFIN_VISION_WORKER` 为 `resources/vision/ElfinVision.exe`。

## 2. 数据目录与恢复

- 源码态桌面：未显式设置 `ELFIN_DATA_DIR` 时沿用 `backend/data`；
- 打包态 / 隔离冒烟 / 已选择恢复副本：`ELFIN_DATA_DIR = selectedData(userData)`（[backend.cjs](../../desktop/backend.cjs#L47-L49)）；`selectedData` 读取 `data-selection.json`，校验：
  - 目标在 `userData/data` 内；
  - `restore-manifest.json` 的 SHA256 与记录一致；
  - `copy_id` 与清单一致；
- 切换恢复副本（[recovery.cjs](../../desktop/recovery.cjs#L2)）：
  1. 调 `/api/backup/activation-plan` 获取计划；
  2. `backend.close()` 必须确认停稳并退出自有进程；
  3. 写 `data-selection.json`（旧文件备份为 `.previous.json`）；
  4. `app.relaunch()` 重启到新目录。

恢复只创建新目录，不覆盖当前数据；运行中任务、凭据、硬件资格不会随恢复生效。

## 3. 生产构建

### 3.1 前端生产构建

`npm --prefix web run build` → [web/scripts/build-production.mjs](../../web/scripts/build-production.mjs)：

1. 准备视觉资产（`prepare-vision-assets.mjs`）；
2. Vite 构建主/视觉入口到 `web/dist`；
3. 用 `vite.remote.config.js` 单独构建 `remote-camera.html` 到 `assets/remote-camera/`；
4. 校验三个入口：不含开发站点跳转、含生产脚本、资源存在、远程入口不含非访客资源；
5. 计算源码输入 SHA256，写入 `web/dist/build-identity.json`（含每个产物的哈希）；
6. 如果构建期间源码变化，直接失败。

### 3.2 桌面打包

`backend/.venv/Scripts/python.exe tools/build_desktop.py`（可加 `--installer`）：

1. 先跑前端生产构建；
2. 计算后端/契约源码指纹；
3. 把 `backend/`、`contracts/`、`desktop/`、`web/dist`、`package.json`、`package-lock.json` 复制到 `output/runtime/desktop-source-*` 临时快照；
4. 校验源目录与快照指纹一致，否则拒绝继续；
5. 写 `runtime-identity.json`；若存在匹配的验收证据则复制 `acceptance.json`；
6. 用 `.venv-vision` PyInstaller 构建 `ElfinVision`（MediaPipe worker）；
7. 用当前 Python 构建 `ElfinBackend`（FastAPI + 页面 + 契约）；
8. 调 electron-builder 生成 `dir` 或 NSIS；
9. 打包后逐文件校验快照哈希，防止打包器改写源码。

产物：

| 路径 | 内容 |
| --- | --- |
| `output/desktop-release/win-unpacked/JiuShuRobotStudio.exe` | 免安装桌面 |
| `output/desktop-release/JiuShu-Setup-<version>.exe` | NSIS 安装包（仅 `--installer`） |
| `output/desktop-engine/ElfinBackend/` | PyInstaller 后端 |
| `output/desktop-vision/ElfinVision/` | PyInstaller 视觉 worker |
| `output/renewal/desktop-build-source.json` | 快照路径、身份、逐文件 SHA256 |
| `output/runtime/runtime-identity.json` | 打包运行身份 |

`package.json` 的 `build.extraResources` 把后端、视觉 worker、品牌图放进 `resources/`；主进程用 `process.resourcesPath` 找图标和 worker。

## 4. 启动脚本

| 命令 | 实际行为 |
| --- | --- |
| `start.bat` / `start.bat dev` / `start.bat restart` | 调 [run_dev.ps1](../../tools/run_dev.ps1)：复用或启动 8710 + 5173 |
| `start.bat stop` | 只停可核验的 5173 Vite 与 8710 本仓库后端 |
| `start.bat build` | `env_doctor.ps1` + `check_frontend.ps1 -ForceBuild` |
| `start.bat package` | `build_desktop.ps1 -Installer` |
| `start.bat web` | 已下线；提示 8710 只提供 API |
| `npm start` | `electron .`，源码态桌面 |
| `npm test` | `node --test desktop/test/*.test.cjs` |

`run_dev.ps1`（[L21-L42](../../tools/run_dev.ps1#L21-L42)）：

- 设 `HUYAN_NO_REAL=1`（开发默认禁真机）；
- 先 `run_backend.ps1 -Ensure -Reload`；
- 检查 5173 是否为本项目（探测 `/@vite/client` 与 `TextWritingTask.vue?raw` 含 `createDrawingTask`）；
- 被未核验进程占用时拒绝重复启动；
- Vite 日志写 `output/runtime/vite-dev.{stdout,stderr}.log`。

`start.bat stop` 只停止可验证归属的进程，不是物理急停。

## 5. 桌面测试

`desktop/test/` 下有 14 个 `*.test.cjs`，覆盖：

| 测试 | 关注点 |
| --- | --- |
| `backend.test.cjs` | 后端会话启动/停止/复用 |
| `build-identity.test.cjs` | 源码/前端身份兼容 |
| `camera-permission.test.cjs` | 相机权限只给自有窗口 |
| `content-cache.test.cjs` | 清缓存不影响偏好/数据 |
| `data-selection.test.cjs` | 恢复目录选择与清单校验 |
| `entryTruthSmoke.test.cjs` | 生产入口真实性 |
| `log.test.cjs` | 有界日志轮转 |
| `manual-arrival-driver.test.cjs` | 人工到达驱动 |
| `python-entry.test.cjs` | 私有后端入口 |
| `qualitySmoke.test.cjs` | 质量冒烟 |
| `recovery.test.cjs` | 恢复副本切换 |
| `robotPlayDriver.test.cjs` | 取放驱动 |
| `smoke-isolation.test.cjs` | 冒烟数据隔离 |
| `taskNavigationDriver.test.cjs` | 任务导航 |

另有 `backend_entry_test.py`、`camera_snapshot_test.py` 为 Python 侧辅助测试，`npm test` 不包含它们。

## 6. 安全边界

1. **渲染器无 Node**：`nodeIntegration:false`、`contextIsolation:true`、`sandbox:true`；只有 preload 暴露的有限 API。
2. **来源校验**：IPC handler 用 `senderWindow` 比对 webContents、URL、主 frame；非法来源直接抛错。
3. **导航锁定**：只允许本站 origin 与 `/`、`/index.html`、`/vision.html`；外部链接一律拒绝。
4. **相机权限最小化**：只允许自有窗口的主 frame 申请 `video`；不 blanket allow。
5. **私有后端密钥**：32 字节随机 key，桌面专属状态/关闭接口必须带 header；外部页面不可调用。
6. **CSP/缓存**：主 frame 注入 CSP；页面响应 `no-store`，避免旧构建残留。
7. **关闭顺序**：先停运动、确认停稳、再退出后端；停止失败窗口保持打开。
8. **数据恢复**：清单哈希/路径越界/副本 ID 三重校验；恢复只切指针，不覆盖原数据。

## 7. 常见故障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 桌面提示"8710 已有本地后端但不提供桌面页面" | `start.bat` 启动的是纯 API 服务，桌面与它共用数据目录 | 先 `start.bat stop`，再点"重试启动"；浏览器开发继续用 `start.bat dev` |
| 后端启动超时 | 依赖缺失/端口/数据目录/日志 | 看 `userData/logs/backend.log`、`output/runtime/backend*.log` |
| 视觉窗口检测不可用 | 缺 `.venv-vision` 或 `ElfinVision.exe` | 安装视觉依赖或重新打包；源码态设置 `ELFIN_VISION_PYTHON` |
| 页面空白/旧页面 | `web/dist` 缺失或构建身份不匹配 | `npm --prefix web run build` 后重新打包 |
| 关闭失败 | 停止未确认、后端仍在退出 | 等停止确认；不要强杀进程后继续操作真机 |
| 恢复副本无效 | 清单哈希/路径/ID 不符 | 重新导出/恢复；不要手工改 `restore-manifest.json` |
