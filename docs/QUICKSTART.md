# 项目上手文档

本页命令是说明，不是本轮已执行记录。先看[STATUS](STATUS.md)和[操作边界](ROBOT_OPERATIONS.md)。只读审查不需要启动项目。

## 1. 准备

在项目根目录使用PowerShell。Python声明为3.11及以上，实际兼容以pyproject与检查为准；Node/npm按锁文件及当前依赖要求准备，不随意升级。
读取package.json、web/package.json、backend/requirements.txt；相机是可选依赖，不为看文档安装SDK。

只检查现有环境的入口：

```powershell
powershell -NoProfile -File tools/env_doctor.ps1 -CheckOnly
node --version
npm.cmd --version
```

CheckOnly不修复环境；不要误用默认体检代替只读，它可能修复虚拟环境/安装依赖。

## 2. 离线开发启动（获得启动授权后）

保存过的自动连接偏好可能存在。只做离线演练时，启动前设置进程级禁真机，不要单凭“默认仿真”判断隔离。

```powershell
$env:HUYAN_NO_REAL = '1'
.\start.bat dev
```

然后访问http://127.0.0.1:5173。该命令可能准备依赖、启动后端热重载与Vite，不是无副作用检查；HUYAN_NO_REAL不禁止摄像头，仍不要主动开启设备。
不要在真机作业期间使用热重载。已有服务可能被复用，不能假定新环境变量改变了已有进程；先核对服务归属与模式，必要操作须另有授权。

## 3. 认识入口差异

| 入口 | 实际含义 |
| --- | --- |
| start.bat（无参数） | 优先已有打包EXE，否则Electron；不保证加载最新源码 |
| start.bat dev | 准备环境并确保后端，Vite开发模式 |
| start.bat build | 可能安装依赖，强制前端构建 |
| start.bat restart | 先检查前端，可能构建，再重启后端并打开桌面，不是纯后端重启 |
| start.bat stop | 优先桌面quit；不保证同时停止独立开发后端，更不是物理急停 |
| start.bat package | 生成发布产物，需独立授权与发布验收 |

当前web/dist首页是开发跳转，不能把8710或桌面入口当作已独立发布可用。相关修复在FIX-025/REN-08；本轮不构建替换。

## 4. 第一次体验

先确认页面仿真/无真实链路，再在场景添加普通物体→点位预览→明确执行仿真目标。功能页可体验现有井字棋/分拣；完整条件见[用户指南](USER_GUIDE.md)。
本地任意3D文件导入与CPU YOLO属于计划，不要寻找不存在的按钮。

## 5. 检查而不启动设备

```powershell
node tools/docs.mjs check
npm.cmd --prefix web test
npm.cmd test
.venv/Scripts/python.exe -B tools/run_offline_checks.py check_ai_intent.py
```

后三项是运行检查并可能生成临时文件/报告，不是零写入。使用前按[开发文档](DEVELOPMENT.md)核对环境，尤其ELFIN_DATA_DIR。本轮只执行第一类文档检查，未重新跑应用测试。
