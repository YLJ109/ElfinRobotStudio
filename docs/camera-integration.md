# 摄像头接入

右上角“摄像头”面板用于本机设备管理。后端会分别识别：

- Intel RealSense：通过官方 `pyrealsense2` 读取设备名称、序列号、固件和可用流。
- 普通 USB 摄像头：通过 OpenCV 枚举本机视频设备。

RealSense 可选择彩色、深度、红外；带 IMU 的 D435i 还会显示陀螺仪和加速度计。打开一台设备前会释放当前设备，关闭按钮会停止所有数据流。扫描操作只探测并释放句柄，不会自动占用摄像头。

## 安装视觉依赖

在项目虚拟环境中执行：

```text
.venv\Scripts\python.exe -m pip install -r sim\backend\requirements-vision.txt
```

RealSense 还需要安装 Intel 官方 librealsense/USB 驱动。没有设备或依赖时，接口仍返回结构化诊断，页面不会伪报“已连接”。

## 接口

- `GET /api/cameras`：读取最近一次扫描和当前开启状态。
- `POST /api/cameras/scan`：扫描并返回设备列表。
- `POST /api/cameras/open`：传入 `camera_id` 与 `streams` 开启指定流。
- `POST /api/cameras/close`：停止当前设备并释放句柄。

`streams` 可使用 `color`、`depth`、`infrared`、`gyro`、`accel`。IMU 流仅对设备实际提供的能力有效。
