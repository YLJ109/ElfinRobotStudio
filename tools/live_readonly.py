"""真机只读核对：连 192.168.0.10，只发 IsSimulation/ReadRobotModel/PackageVersion/ReadCurFSM
四条只读指令，其余全部来自 10004 的 Data Sheet 推送。不发任何上电/使能/运动指令。

跑法： python tools/live_readonly.py [IP]
"""

from __future__ import annotations

import faulthandler
import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PyQt6.QtWidgets import QApplication  # noqa: E402

from huayan_panel.core.robot import Perm  # noqa: E402
from huayan_panel.ui import lamps  # noqa: E402
from huayan_panel.ui.main_window import MainWindow  # noqa: E402

import app as APP  # noqa: E402  用和正式入口一样的主题，截图才是真实长相

HOST = sys.argv[1] if len(sys.argv) > 1 else "192.168.0.10"


def spin(app, ms):
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.01)


def main():
    faulthandler.enable()
    faulthandler.dump_traceback_later(15, repeat=True)

    def stage(m):
        print("STAGE " + m, flush=True)

    stage("QApplication")
    app = QApplication(sys.argv)
    APP.apply_theme(app)
    stage("MainWindow")
    win = MainWindow()
    stage("show")
    win.show()
    link = win.link
    link.log.connect(lambda k, m: print(f"[{k}] {m}", flush=True))
    assert link.perm == Perm.READONLY, "本脚本必须停在只读许可"

    stage("spin300")
    spin(app, 300)
    stage("connect")
    win.ip.setText(HOST)
    win._connect()
    stage("spin6s")
    spin(app, 6000)
    stage("dump")

    s = link.snap
    print("=" * 68, flush=True)
    print(f"取流 {s.stream_up}  命令 {s.cmd_up}  帧率 {s.rate_hz}Hz  控制器时间 {s.update_time}")
    print(f"型号 {s.model}  版本 {s.version}  性质 {s.is_sim}  FSM {s.robot_state}")
    print(f"关节角 {['--' if v is None else round(v, 3) for v in s.joints]}")
    print(f"基座坐标 {['--' if v is None else round(v, 3) for v in s.pcs_base]}")
    print(f"使能 {s.enabled}  运动 {s.moving}  到位 {s.inpos}  速度比 {s.override}")
    print(f"错误 轴{s.err_axis} 码{s.err_code}  各轴错误码 {s.axis_err}")
    print(f"抱闸 {s.brake}  缩减模式 {s.reduce_mode}  拖拽模式 {s.free_drive}")
    print(f"温度 {s.temps}")
    print(f"电流 {s.currents}")
    print(f"48V 进 {s.volt48_in}  出 {s.volt48_out}")
    print(f"EtherCAT fps {s.ecat_fps}  丢帧 {s.ecat_lost}  发错 {s.ecat_tx_err}  收错 {s.ecat_rx_err}")
    # 只读复核一次 IO（Read* 指令，不写任何输出）
    win._io_refresh_read()
    spin(app, 2500)
    print(f"IO 即时复核: EndDO={s.end_do} EndDI={s.end_di} EndButton={s.end_button} "
          f"EnableEndBTN={s.enable_end_btn} EndAI={s.end_ai}", flush=True)
    print(f"           BoxDO={s.box_do} BoxDI={s.box_di} BoxCI={s.box_ci} BoxCO={s.box_co} "
          f"Conveyor={s.conveyor} Encode={s.encode}", flush=True)
    print("-" * 68, flush=True)
    for name, level, detail, src in lamps.rules(s, link.perm, False):
        print(f"{level.upper():6} {name:14} {detail}   [{src}]", flush=True)
    print("=" * 68, flush=True)

    stage("re-show")
    win.show()
    spin(app, 500)
    stage("grab")
    shot = f"tools/live_{HOST.replace('.', '_')}.png"
    win.grab().save(shot)
    stage("screenshot -> " + shot)

    live = s.stream_up and s.cmd_up
    stage("close")
    win.close()
    link.disconnect()
    stage("disconnect done")
    spin(app, 1000)
    return 0 if live else 1


if __name__ == "__main__":
    sys.exit(main())
