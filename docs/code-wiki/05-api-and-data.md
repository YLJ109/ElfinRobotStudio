# 05 · API、数据与安全边界

本文说明后端对外契约、数据文件格式、访问控制和备份恢复。所有接口都以"当前代码"为准；接口存在不代表现场设备支持，也不代表已验收。

## 1. 启动参数与端口

| 项 | 默认 | 说明 |
| --- | --- | --- |
| FastAPI 端口 | `8710` | 开发/独立后端；只提供 API，不托管网页（[main.py](../../backend/main.py#L1300)） |
| Vite 端口 | `5173` | 唯一网页入口；代理 `/api` 与 `/ws` 到 8710（[vite.config.js](../../web/vite.config.js)） |
| 桌面私有后端 | `127.0.0.1:0` | 随机端口，由 Electron 解析 stdout 得到（[backend_entry.py](../../desktop/backend_entry.py#L69-L72)） |
| 真机命令/状态 | `10003 / 10004` | 项目文档约定的控制器端口；其他品牌不适用 |
| 远程访客 | 同源 HTTPS | 单向 WebRTC，只读，无控制 API |

## 2. 数据目录

默认 `backend/data`；可用 `ELFIN_DATA_DIR` 覆盖（[paths.py](../../backend/paths.py#L8)）。源码态桌面未显式设置时沿用该默认；打包态、隔离冒烟或已选择恢复副本时，Electron 会把 `ELFIN_DATA_DIR` 指向 `userData/data` 或 `data-selection.json` 的子目录。

### 2.1 文件清单

| 文件/目录 | 格式 | 写入方 | 作用 |
| --- | --- | --- | --- |
| `settings.json` | 对象 | `SettingsStore` | 模式、速度、限位、围栏、残影、AI、日志保留等 |
| `points.json` | 数组 | `PointStore` | 点位关节角、TCP、来源、编号 |
| `points_meta.json` | 对象 | `PointStore` | 已预留的下一个 P 编号 |
| `objects.json` | 数组 | `ObjectStore` | 当前场景摆放物 |
| `scenes.json` | 对象 `{version,items}` | `SceneStore` | 多现场布局，只含仿真道具 |
| `scene_assets/` | 目录 | `SceneAssetStore` | 导入的受控 GLB + 元数据 |
| `tools.json` | 对象 `{version,items,revisions}` | `ToolStore` | 工具档案与修订历史 |
| `mounted_tool.json` | 对象 | `ToolStore` | 当前安装工具身份/来源/状态 |
| `calibrations.json` | 对象 | `CalibrationStore` | 工位/TCP/IO 标定 |
| `programs.json` | 对象 `{name:{src,updated}}` | `ProgramStore` | 已保存程序库 |
| `run-records.json` | 对象 `{version,runs}` | `RunJournal` | 有界运行记录（最多 64 条/2MiB） |
| `exec_state.json` | 对象 | `World._persist_exec` | 上次执行进度；只报位置，不自动续跑 |
| `sim_pose.json` | 对象 | `World._save_sim_pose` | 仿真关机姿态，下次启动恢复 |
| `studio_recordings.json` | 对象 | 旧工艺录制 | 历史只读兼容 |
| `vision_workspace.json` | 对象 `{config,camera_model_version}` | `VisionWorkspace` | 视觉配置；检测开关启动时强制关 |
| `events.jsonl` | JSONL | `EventLog` | 追加式事件日志；清空只重置内存/文件 |
| `control.lock` | 1 字节锁 | `ServiceLease` | 保证单控制进程 |

### 2.2 点位格式

来源 [points.py](../../backend/core/points.py#L63-L81)：

```json
{
  "id": 791717635586,
  "code": "P1",
  "name": "P1",
  "joints": [0, 0, 0, 0, 0, 0],
  "tcp": {"xyz_mm": [0, 0, 1195], "rpy_deg": [0, 0, 0], "link": "elfin_end_link"},
  "source": "sim",
  "note": "示教录入",
  "created": 1791717635.6
}
```

规则：

- `joints` 必须是 6 个有限数值；
- `code` 固定为 `P<n>`，先写 `points_meta.json` 预留再写点位，失败允许跳号但不复用；
- 名称 1–80 个可见字符，不能与编号/其他名称冲突；
- 只允许通过接口改名称；
- `source` 用于说明来源，来源不明点位执行前仍要经过上下文/安全检查。

### 2.3 物体格式

来源 [objects.py](../../backend/core/objects.py#L26-L56)、[schemas.py](../../backend/schemas.py#L12-L38)：

```json
{
  "id": "…",
  "name": "A 原料区",
  "kind": "box | sphere | cylinder",
  "xyz": [450, -350, 2],
  "size": [330, 330, 4],
  "gravity": false,
  "floating": true,
  "collide": false,
  "floor": true,
  "rotation_deg": [0, 0, 0],
  "scale": 1,
  "locked": false,
  "preset": "zone_a",
  "surface": "plastic",
  "color": "#606b73",
  "task": {"group": "...", "mode": "chess", "role": "zone_a", "symbol": "", "home": 0, "slot": -1}
}
```

- `preset` 必须在白名单且与碰撞代理形状一致；
- `surface` 必须在白名单；颜色 `#RGB`/`#RRGGBB`；
- 旋转 ±3600°、缩放 0.01–100、锁定布尔；
- `task` 只用于任务分组/角色/格位，不改变碰撞代理；
- 导入 GLB 只允许 `kind=box`，碰撞代理仍是保守箱体。

### 2.4 场景格式

来源 [scenes.py](../../backend/core/scenes.py#L49-L97)：

```json
{
  "version": 1,
  "items": {
    "演示 · 01 三色分拣": {
      "name": "演示 · 01 三色分拣",
      "objects": [ /* 与 objects.json 同格式 */ ],
      "created_at": 0,
      "updated_at": 0
    }
  }
}
```

- 最多 1000 个物体；名称不能含 `\ / : * ? " < > |`；
- 每个物体保存前走同一 sanitizer/接触校验；
- 导入先"读文件/解析/结构校验"，失败不改变当前场景；
- 现场文件只保存仿真布局，不含真机状态。

### 2.5 程序格式

来源 [program_store.py](../../backend/core/program_store.py)、[programs.json](../../backend/data/programs.json)：

```json
{
  "01 爱心·仿真": {
    "src": "# 注释\nMOVEJ ...\n",
    "updated": 1793423805.4
  }
}
```

程序语法两套：ElfinScript 行指令与受限 Python 子集；两者都由后端校验，前端编辑器只做文本/高亮。

### 2.6 工具格式

来源 [tools.py](../../backend/core/tools.py#L21-L57)：

```json
{
  "id": "suction-template",
  "name": "吸盘（模板）",
  "type": "suction",
  "revision": "r1",
  "identity": {"source": "manual", "hardware_id": null},
  "mount": {"translation_mm": [0,0,0], "quaternion_xyzw": [0,0,0,1]},
  "tcp": {"name": "suction-center", "translation_mm": [0,0,120], "rpy_deg": [0,0,0], "status": "unvalidated"},
  "payload": {"mass_kg": null, "com_mm": null, "status": "unknown"},
  "actions": {"vacuum_on": "unmapped", "vacuum_off": "unmapped", "blow_on": "unmapped", "blow_off": "unmapped"},
  "io_mapping": {},
  "verification": "template"
}
```

身份来源 `builtin/manual/detected/controller_config` 分开保存；模板 TCP/负载是"未验证"，不能当现场标定。

### 2.7 运行记录格式

来源 [run_journal.py](../../backend/application/run_journal.py#L30-L40)、[run-records.json](../../backend/data/run-records.json)：

```json
{
  "version": 1,
  "runs": [
    {
      "run_id": "…",
      "phase": "finished | aborted | error | interrupted | replaced",
      "context": {
        "session_id": "…", "mode": "sim", "config_revision": 1,
        "scene_revision": "sha256", "program_hash": "sha256",
        "tool_revision": "sha256", "executor_state": "completed",
        "plan_id": "…", "group": "…", "action": "…"
      },
      "started_at": 0, "updated_at": 0, "ended_at": 0, "elapsed_s": 0,
      "confirmed_steps": 0, "last_confirmed": {"index":0,"line":1,"kind":"movej"},
      "current_step": {"index":0,"line":1,"kind":"movej"},
      "reason": "", "partial": false, "analysis_only": true
    }
  ]
}
```

`analysis_only` 表示它是观察证据，不是重新执行许可。

### 2.8 视觉配置格式

来源 [vision_workspace.py](../../backend/core/vision_workspace.py#L14-L57)：

```json
{
  "config": {
    "enabled": true,
    "detection_enabled": false,
    "input": "camera | image | virtual | remote",
    "colors": ["red","yellow","green","blue"],
    "min_area": 200,
    "roi": [0.02,0.02,0.96,0.96],
    "height_mm": 20,
    "mapping": false,
    "virtual": {"offset_mm":[-36,0,31],"direction":[0,0,1000],"fov":55,"width":640,"fps":15}
  },
  "camera_model_version": 7
}
```

`detection_enabled` 启动时强制 `false`，采集/推理需要用户显式开启。

## 3. REST 约定

### 3.1 通用响应

- 成功：`{"ok": true, ...}` 或直接对象；
- 业务拒绝：`{"ok": false, "why": "中文原因"}`；
- 参数校验失败：FastAPI 422，`detail` 数组；全局 `ValueError` → `{"ok":false,"why":...}` 422；
- 冲突/状态不允许：409（例如"仍有持件/未停稳/已在执行"）；
- 请求体过大：413；接收超时：408；
- 所有 `/api/*` 响应 `Cache-Control: no-store`。

### 3.2 接口分组

| 组 | 代表端点 | 说明 |
| --- | --- | --- |
| 状态/身份 | `GET /api/state`、`/api/build`、`/api/acceptance`、`/api/meta`、`/api/fields`、`/api/capabilities` | 快照、构建/验收指纹、能力与字段来源 |
| 设置/速度 | `GET/POST /api/settings`、`POST /api/speed` | 设置白名单；速度只改当前模式那一路 |
| 日志 | `GET /api/logs`、`/api/logs/export.csv`、`POST /api/logs/clear` | 增量/过滤/导出/清空 |
| 电源/控制 | `POST /api/control`、`/api/ready`、`/api/ready/cancel`、`/api/power`、`/api/free`、`/api/estop` | 一键就绪、使能、手拖、急停 |
| 运动 | `POST /api/jog`、`/api/movej`、`/api/preview`、`/api/ik`、`/api/fk` | 点动、目标、预览、正逆解 |
| 点位/示教 | `GET/POST /api/points`、`POST /api/points/teach`、`PATCH/DELETE /api/points/{id}`、`POST /api/points/run`、`/api/points/stop`、`/api/points/resume`、`POST /api/teaching/batch` | 点位执行与批量示教 |
| 程序 | `GET/POST /api/programs`、`GET/DELETE /api/programs/{name}`、`POST /api/program/load`、`validate`、`start`、`stop`、`pause`、`resume`、`resume_last`、`forget_resume` | 程序库与运行控制 |
| 场景/物体 | `/api/objects`、`/api/objects/update`、`remove`、`clear`、`/api/scenes`、`/api/scenes/load`、`import`、`/api/scenes/export/{name}`、`/api/workspace/scene/*` | 物体、现场、模板/阵列/对齐/撤销 |
| 工具/IO | `/api/tools`、`/api/tools/select`、`clear`、`action`、`profile`、`io-confirm`、`/api/io/enddo`、`probe`、`/api/sim/input` | 工具档案、吸盘动作、仿真 IO |
| 相机 | `/api/cameras`、`scan`、`open`、`close`、`frame.jpg`、`stream.mjpg`、`pointcloud`、`measure` | 采集与只读测距 |
| 视觉 | `/api/vision/markers`、`/api/vision/workspace/*`、`/api/vision/pipeline`、`/api/vision/tasks`、`/api/vision/remote/*` | 识别、标定、任务、远程 |
| 任务 | `/api/play`、`/api/play/program/*`、`/api/drawing/*`、`/api/challenge/handling/*`、`/api/assembly/*` | 分拣/棋局/取放/绘制/闯关/装盒 |
| 真机 | `/api/real/connect`、`disconnect`、`ask`、`/api/real/hand-follow/status`、`start`、`stop` | 受约束真机接入 |
| AI | `/api/ai/config`、`test`、`chat`、`run`、`revoke`、`explain_error`、`/api/plans/inspect`、`execute` | 白名单计划与解释 |
| 证据 | `/api/runs`、`/api/runs/{id}`、`/api/runs/{id}/export`、`/api/backup/*`、`/api/blackbox`、`/api/trend`、`/api/playback` | 运行记录、备份、诊断、趋势、回放 |
| 调试 | `/api/program/debug/*` | 旧接口仍挂载；当前前端无调用方（[TODO](../TODO.md)） |

### 3.3 关键语义

- `POST /api/preview` 只生成残影与 `preview_safety`，**不运动**。
- `POST /api/movej` / `/api/points/run` / `/api/program/start` 才是运动提交。
- `POST /api/motion/pause|resume|cancel` 是任务级控制，必须有可验证状态。
- `POST /api/control` 的 `key` 由后端 `controls.derive()` 按当前状态推导；前端只按当前按钮。
- `POST /api/ready` 是时序泵：逐步发指令并等待 FSM/使能回读，不是"一次发完就算"。
- `/api/backup/preview` 是唯一允许 32MiB 请求体的端点，其余 2MiB。

## 4. WebSocket 协议

端点：`/ws`（[main.py](../../backend/main.py#L1244)）。

### 4.1 服务端 → 客户端

首帧：

```json
{"type":"state","data": <完整快照，含 telemetry>}
```

后续：

```json
{"type":"state","data": <快照，settings 可选，log_delta 代替 log>}
{"type":"ping"}
```

- `settings` 只在 `config_rev` 变化时出现；
- `log_delta` 形如 `{"seq": n, "gap": false, "rows": [...]}`；`gap=true` 表示客户端应清空重建；
- 30 秒无快照发 `ping`，客户端可据此判断连接仍活。

### 4.2 客户端约束

- 不在 WS 上发命令；控制走 REST。
- 断线/1.5 秒无新帧 → `stateFresh=false`，前端目标失效、显示"最后状态"。
- 慢客户端只拿最新帧；队列满时丢旧帧。

## 5. 访问控制

### 5.1 HTTP

[main.py](../../backend/main.py#L110-L161) 与 [access.py](../../backend/access.py)：

1. **Host 白名单**：`localhost`、`127.0.0.1`、`::1`，加上 `HUYAN_ALLOWED_HOSTS` 逗号列表；其他 Host → 400。
2. **非回环请求**：必须带 `x-huayan-token`，与 `HUYAN_TOKEN` 常量时间比较；否则 403。
3. **写请求 Origin**：有 Origin 时，其 host 必须等于请求 Host，否则 403（挡跨站写控制口）。
4. **远程只读访客**：仅 `remote-camera.html` 与 `assets/remote-camera/*` 的 HTTPS GET；POST 仅 `VISITOR_PATHS`（配对加入等）例外。
5. **安全响应头**：`X-Content-Type-Options: nosniff`、`X-Frame-Options: DENY`、API `no-store`。
6. **请求体**：默认 2MiB；备份预检 32MiB；超时 10 秒。

### 5.2 WebSocket

- Host 必须可信；
- 非本机必须带 token；
- Origin 必须与 Host 一致；
- 不通过就 `close(code=4403)`。

### 5.3 桌面私有 API

[backend_entry.py](../../desktop/backend_entry.py#L30-L48)：

- `/api/desktop/status`、`/api/desktop/shutdown` 必须带 `x-elfin-desktop-key`；
- key 由 Electron 生成，长度 ≥32；
- 关闭接口先确认停止，再允许退出。

### 5.4 远程相机契约

[contracts/remote-camera.json](../../contracts/remote-camera.json)：

- 操作端：`GET /state`、`POST /pair`、`POST /revoke`；
- 访客：`POST /join`、`POST /offer`、`POST /heartbeat`、`POST /leave`；
- 访客路由需同源 HTTPS；join 消耗一次性配对码；其余用绑定 origin/session 的 Bearer token；
- 限制：配对 120 秒/5 次、会话 900 秒、心跳 15 秒、SDP 64KiB、1280×720、15fps、1500kbps、单视频轨；
- 远程结果只读，不能驱动取放；`captured_at` 为空，只有接收时间/年龄，`capture_age_available=false`。

## 6. 契约文件

| 文件 | 作用 |
| --- | --- |
| [contracts/tool-actions.json](../../contracts/tool-actions.json) | 工具动作 ID、中文名、别名、适用工具类型 |
| [contracts/scene_bounds.json](../../contracts/scene_bounds.json) | 场景物体几何扩展比例/坐标系/范围 |
| [contracts/remote-camera.json](../../contracts/remote-camera.json) | 远程相机路由、认证、限制、生命周期 |

这些 JSON 会进入源码指纹（[build_identity.py](../../backend/build_identity.py#L8-L12)），修改后前后端身份都会变化。

## 7. 备份与恢复

[application/data_backup.py](../../backend/application/data_backup.py#L197)：

- **导出**：打包 sections：`points/objects/scenes/tools/io/settings/programs/assets`；
- **预检**：显示将要变更的差异；不直接覆盖；
- **恢复副本**：`POST /api/backup/restore-copy` 只创建新目录 + `restore-manifest.json`，不改当前 World；
- **激活计划**：`POST /api/backup/activation-plan` 返回需要重启的副本信息；
- **安全**：拒绝凭据字段、路径越界、非法名称；失败保留原选择与证据；
- **桌面**：必须由自有后端执行，确认停稳/退出后才切换指针。

## 8. 错误与观测

| 观测点 | 位置 |
| --- | --- |
| 事件日志 | `events.jsonl`、`GET /api/logs`、CSV 导出 |
| 运行记录 | `run-records.json`、`/api/runs` |
| 诊断包 | `GET /api/blackbox`（关节帧/事件/字段） |
| 遥测 | `/api/state` 的 `telemetry`、`/api/trend`、`/api/playback` |
| 构建身份 | `/api/build`、`web/dist/build-identity.json` |
| 验收记录 | `/api/acceptance`、`web/dist/acceptance.json` |
| 启动日志 | `output/runtime/backend*.log`、桌面 `userData/logs/backend.log` |

**不要**把日志/模型输出当命令，也不要在日志里写密钥、token 或完整现场数据。
