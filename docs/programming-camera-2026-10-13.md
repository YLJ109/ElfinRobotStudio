# 编程、点位预览与真实相机更新

## 已实现

- 原生 CodeMirror 编辑器，默认中文补全，可切英文；拼音首字母、英文前缀和中文前缀匹配。新指令补全插入函数括号，光标进入参数位置；保留旧文件的空格指令格式。支持函数、变量、容器方法补全、四空格块缩进、括号配对与匹配高亮。
- Python 风格变量、数字、字符串、布尔值、列表、字典、索引、切片、解包、赋值、算术、逻辑比较、嵌套 for/while/if/elif/else、函数与返回值、break/continue/pass。中文关键字映射和英文关键字可以混用。
- 后端 AST 解释器实际执行控制流，调用现有运动执行器；无 eval/exec、导入、文件或网络访问。计算分批让出主循环，运动完成才执行下一动作；暂停保留上下文，停止终止后续动作。
- 保存点位固定编号 P1/P2…，按编号、名称、ID 解析。旧数据迁移保留原 P 编号；改名不改变编号和坐标；新编号不复用已删除编号。改名只允许修改名称。
- 运动参数位置列出保存点候选，显示名称与六轴值。编辑器识别编号和引用名称，旁边提供「影」。选择后显示编号、名称、J1–J6 以及 X/Y/Z/A/B/C。预览只登记目标残影，不发送运动命令。
- MOVEL/直线移动/直线运动均支持保存点：读取该点保存的 TCP 位姿。MOVEJ 使用保存的关节角。直线终点残影不代表整条路径已验证。
- 系统视频设备直接枚举名称、编号和设备路径，不再用 0..7 猜测摄像头。列表可能包含物理与虚拟视频设备；未提供名称和打不开的设备如实说明。
- 普通摄像头真实 JPEG 连续预览。RealSense 按 SDK 实际能力提供彩色、深度、左右红外、陀螺仪和加速度流；状态收到首帧后才标为采集中。
- RealSense 深度图点击测距，使用深度比例和 SDK 内参反投影；点云来自 SDK 深度帧，支持交互三维查看和 PLY 导出。点云最多约 6000 点，约 4Hz；图像编码约 10Hz，独立采集线程不阻塞机器人主循环。
- 关闭相机释放设备、撤销图像 URL 和三维资源；旧会话、断流与过期图像、深度、点云不会当作实时数据显示。

## 使用

1. 在点位页保存点位；点击名称后的铅笔改名。
2. 程序里输入 `关节移动(P1)` 或 `直线运动("取料点")`，点击引用旁的「影」查看终点。
3. 头部「规则」打开关于页，那里有完整中英文机器人指令、关键字、内置函数、容器方法和示例。
4. 编辑 → 解析 → 启动。修改代码、参数或点位后重新解析。程序可以命名保存和载入。
5. 三维区右上角摄像头按钮 → 刷新 → 选择设备和流 → 开启。RealSense 有有效深度时才能测距或查看点云。

示例需已有 P1 和名称为「取料点」的点位：

```python
配置 = {"速度": 15, "次数": 2}
路径 = [P1, "取料点"]

定义 到点(目标):
    关节移动(目标)
    等待(0.2)

速度(配置["速度"])
循环 次数 在 范围(配置["次数"]):
    循环 点 在 路径:
        到点(点)
```

英文对应：`def` / `for` / `in` / `range` / `MOVEJ` / `WAIT` / `SPEED`。中文标识符不必翻译。旧式 `LOOP … END` 保留但不支持嵌套；新式循环用冒号和四空格缩进，不能与旧式控制流混写在同一个文件里。

## 数据口径与限制

- 关节角来自保存记录，真机示教时来自有效控制器关节数据；仿真来源明确标为仿真。TCP 默认由保存角度正运动学计算，界面明确标注，并非独立传感器测量。旧点没有完整 TCP 时不会编造坐标。
- 当前脚本是受限的机器人程序语言，不是完整 Python，也不是厂商示教器文件格式。上限：10000 语法节点、100000 次计算、单循环 10000 次、16 层函数调用、容器/字符串长度 10000。不支持类、推导式、模块、异步、装饰器、默认/可变函数参数、任意对象属性。
- 脚本总步数由运行时决定，显示已执行步数。服务重启后不从残缺的线性步骤恢复变量状态；可以取回上次代码，重新解析从头运行。检查点保存源码和计数，避免反复落盘全部历史步骤。
- 深度 XYZ、点云坐标为相机光学坐标系，单位分别 mm / m；尚未加入相机到机器人基座的手眼标定，不能直接当成机器人运动目标。
- 摄像头关闭面板只收起画面；关闭摄像头按钮才释放采集设备。无 RealSense 时不会显示模拟深度或点云。

## 验证记录

- `check_python_program.py`：18 项解释器检查通过。
- `check_program_integration.py`：隔离仿真执行两个目标点，检查函数、列表循环、最终角度、停止响应、改名、旧数据迁移、编号不复用和动态脚本检查点。
- `check_program_languages.py` 5 项、`check_execution.py` 17 项、`check_camera_frames.py` 6 项、`check_features.py` 12 项通过；后端原有冒烟检查 62 项通过。
- 浏览器编辑器 31 项、点位与相机流程 18 项、Python 编辑体验 7 项通过；摄像头面板在 6 种视口通过边界和遮挡检查。浏览器检查不发送机器人运动命令。
- 实际 Windows 枚举 4 个设备：Chicony USB2.0 Camera、DroidCam Source 3、未提供名称设备、OBS Virtual Camera。前两路取到可解码并持续更新的图像；后两路无法打开。浏览器实测 Chicony 640×480 图像显示成功，关闭释放设备。
- 没有连接 RealSense 硬件，深度、红外、IMU、多流组合、点云完整采集尚待实机验证。内参反投影、深度比例、过期数据和会话隔离已做 SDK/接口检查。
- 所有运动测试在复制数据和 `HUYAN_NO_REAL=1` 的隔离环境内完成；没有操纵真机器人。
- 新版已构建，并在 8710 启动；沿用保存的仿真模式和关闭自动连接设置。启动检查发现用户保存现场的 box-a 与 elfin_link1 碰撞，仿真处于安全暂停；未擅自修改现场物体。执行前应在摆放页调整此现场。

## 接口与依据

- `POST /api/cameras/scan`；`POST /api/cameras/open`：`camera_id`、`streams`；`POST /api/cameras/close`；`GET /api/cameras`。
- `GET /api/cameras/frame.jpg?session=…&stream=color|depth|infrared|infrared2`：实际 JPEG；无有效图像返回 503。
- `GET /api/cameras/pointcloud?session=…`：`xyz_m`、`rgb`、`coordinate_frame`、`timestamp`。
- `GET /api/cameras/measure?session=…&x=…&y=…`：深度距离、相机 XYZ、像素位置。
- `PATCH /api/points/{id}`：仅 `name`；运行或暂停的程序存在时先停止再改名。

官方参考：[RealSense Python pipeline](https://realsenseai.github.io/librealsense/python_docs/_generated/pyrealsense2.pipeline.html)、[SDK 点云示例](https://github.com/realsenseai/librealsense/blob/master/wrappers/python/examples/opencv_pointcloud_viewer.py)、[投影与坐标定义](https://github.com/realsenseai/librealsense/wiki/Projection-in-RealSense-SDK-2.0)、[系统相机枚举库](https://github.com/lukehugh/cv2_enumerate_cameras)。
