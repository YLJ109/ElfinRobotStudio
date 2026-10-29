# 摄像头接入

## 2026-10-29 USB 链路核对

- 重新枚举D435i仍报告USB2.1、固件5.15.1.55；Chicony保持采集中。未开关相机或操作机器人。USB3物理接口与当前实际协商速率不同，不能据此断言接口不是3.0，也不能直接认定线材损坏。
- 优先用确认支持USB3 SuperSpeed（5Gbps及以上）的短数据线直连；Type-C外形和充电功率不等于数据速率。排除延长线、转接和集线器，每次只换一个条件，重新加载后比较USB回读。
- 官方同型号案例换原配线后恢复：[SDK issue13903](https://github.com/realsenseai/librealsense/issues/13903)。这不是本机根因证明。
- 厂商勘误RSDSO-6804/8681也记录插入USB3接口偶发枚举成USB2；Type-A主机接法建议先接相机Type-C端，再接电脑端：[Specification Update 039，第13页](https://realsenseai.com/wp-content/uploads/2025/02/RealSense-D400-Series-Spec-Update-039.pdf)。不要强插，不自动重置设备、升级固件、卸载驱动或更改系统设置。换已知USB3线后仍为2.1，应继续对照其他直连端口与电脑。

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
