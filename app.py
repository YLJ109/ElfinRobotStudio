"""华沿 Elfin V6 手动控制台入口。

用法：
    python app.py [--host 192.168.0.10] [--mock]
"""

from __future__ import annotations

import argparse
import os
import sys
import traceback

from PyQt6 import QtGui
from PyQt6.QtCore import QLockFile
from PyQt6.QtGui import QFont, QPalette
from PyQt6.QtWidgets import QApplication, QMessageBox

from huayan_panel.ui.main_window import MainWindow
from huayan_panel.ui import theme as T


def apply_theme(app: QApplication):
    app.setStyle("Fusion")
    c = QtGui.QColor
    pal = QPalette()
    pal.setColor(QPalette.ColorRole.Window, c(T.BG))
    pal.setColor(QPalette.ColorRole.Base, c(T.PANEL))
    pal.setColor(QPalette.ColorRole.Text, c(T.TEXT))
    pal.setColor(QPalette.ColorRole.Button, c(T.PANEL))
    pal.setColor(QPalette.ColorRole.ButtonText, c(T.TEXT))
    pal.setColor(QPalette.ColorRole.Highlight, c(T.ACCENT_DIM))
    app.setPalette(pal)
    app.setStyleSheet(T.QSS)
    f = QFont()
    f.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "Segoe UI"])
    f.setPointSize(10)
    app.setFont(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="192.168.0.10")
    ap.add_argument("--mock", action="store_true", help="连接本地假服务器 127.0.0.1，用于无真机自测")
    a = ap.parse_args()

    app = QApplication(sys.argv)
    lock_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "huayan-panel.lock")
    lock = QLockFile(lock_path)
    # 上一实例被强杀时锁文件会留下；2s 后视为过期并自动接管，否则永远启动不了
    lock.setStaleLockTime(2000)
    if not lock.tryLock(2500):
        holder = lock.pid()
        QMessageBox.warning(None, "已在运行",
                            f"已有一个控制面板实例在运行（PID {holder}）。\n\n"
                            "机器人命令端口疑似每端口只允许一个客户端，多开会互相抢连接。\n"
                            f"若那个进程已经不存在，删掉这个文件即可：\n{lock_path}")
        return 2

    apply_theme(app)
    w = MainWindow()
    w.ip.setText("127.0.0.1" if a.mock else a.host)
    w.resize(1560, 980)
    w.show()
    if a.mock:
        w._connect()
    code = app.exec()
    w._disconnect()
    lock.unlock()
    return code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit:
        raise
    except Exception:
        # 双击 .bat 启动时崩了不能一闪而过，否则看不到原因
        traceback.print_exc()
        input("按回车退出…")
