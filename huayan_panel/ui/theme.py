"""深色工业 HUD 主题：青绿主色 + 切角 + 角括号描边 + 中英双层级标签。"""

ACCENT = "#48b7af"
ACCENT_DIM = "#2c736e"
BG = "#0b1416"
PANEL = "#10201f"
PANEL_2 = "#16292733"
LINE = "#1f3b38"
TEXT = "#cfe9e6"
TEXT_DIM = "#7fa8a4"
WARN = "#e0a33b"
DANGER = "#e4553f"
OK = "#5fce8b"

QSS = f"""
QWidget {{ background: {BG}; color: {TEXT};
          font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Noto Sans SC", "Segoe UI", sans-serif;
          font-size: 13px; }}
QLabel#title {{ color: {ACCENT}; font-size: 20px; font-weight: 700; letter-spacing: 2px; }}
QLabel#sub {{ color: {TEXT_DIM}; font-size: 11px; letter-spacing: 3px; }}
QLabel#metric {{ color: {ACCENT}; font-size: 17px; font-weight: 700; font-family: Consolas, monospace; }}
QLabel#metricDim {{ color: {TEXT_DIM}; font-size: 12px; }}
QLabel#danger {{ color: {DANGER}; font-weight: 700; }}
QLabel#warn {{ color: {WARN}; font-weight: 700; }}
QLabel#ok {{ color: {OK}; font-weight: 700; }}
QGroupBox {{ background: {PANEL}; border: 1px solid {LINE}; border-radius: 2px;
             margin-top: 14px; padding: 10px 8px 8px 8px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; padding: 0 6px;
                    color: {ACCENT}; font-weight: 700; letter-spacing: 1px; }}
QPushButton {{ background: #14292733; color: {TEXT}; border: 1px solid {ACCENT_DIM};
               padding: 6px 10px; border-radius: 2px; }}
QPushButton:hover {{ background: #1b3835; border-color: {ACCENT}; }}
QPushButton:pressed {{ background: {ACCENT_DIM}; }}
QPushButton:disabled {{ color: #4d6563; border-color: #233a38; background: #0e1a1a; }}
QPushButton[role="danger"] {{ background: #2a1211; border: 1px solid {DANGER}; color: #ffb4a6;
                              font-weight: 700; }}
QPushButton[role="danger"]:hover {{ background: #451a17; }}
QPushButton[role="danger"]:disabled {{ color: #6b4441; border-color: #3a2422; }}
QPushButton[role="estop"] {{ background: #4a1512; border: 2px solid {DANGER}; color: #ffd9d2;
                              font-size: 19px; font-weight: 800; padding: 14px; letter-spacing: 4px; }}
QPushButton[role="estop"]:hover {{ background: #6d201a; }}
QPushButton[role="arm"] {{ border: 1px solid {WARN}; color: #ffdca8; }}
QPushButton:checked {{ background: {ACCENT_DIM}; border-color: {ACCENT}; color: #eafffd; }}
QLineEdit, QComboBox, QDoubleSpinBox {{ background: #0d1c1b; border: 1px solid {LINE};
                                        padding: 4px 6px; border-radius: 2px; color: {TEXT}; }}
QLineEdit:focus, QComboBox:focus {{ border-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 6px; background: #122523; border: 1px solid {LINE}; }}
QSlider::sub-page:horizontal {{ background: {ACCENT_DIM}; }}
QSlider::handle:horizontal {{ width: 14px; margin: -6px 0; background: {ACCENT}; border-radius: 2px; }}
QSlider::handle:horizontal:disabled {{ background: #37514f; }}
QPlainTextEdit {{ background: #08100f; border: 1px solid {LINE}; color: #a9d4d0;
                  font-family: Consolas, monospace; font-size: 12px; }}
QStatusBar {{ background: {PANEL}; color: {TEXT_DIM}; }}
QToolTip {{ background: #0d1c1b; color: {TEXT}; border: 1px solid {ACCENT}; }}
"""

# 面板切角 + 角括号，靠绘制实现，不用图片
CORNER = 14


def paint_hud_frame(painter, rect, accent=ACCENT, bracket=18):
    """在 rect 上画切角边框和四角括号。"""
    from PyQt6.QtCore import QPointF, Qt
    from PyQt6.QtGui import QColor, QPen

    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    c = CORNER
    path = None
    pts = [
        QPointF(x + c, y), QPointF(x + w, y), QPointF(x + w, y + h - c),
        QPointF(x + w - c, y + h), QPointF(x, y + h), QPointF(x, y + c),
    ]
    painter.save()
    painter.setPen(QPen(QColor(accent), 1))
    prev = QPointF(x, y)
    for p in pts:
        painter.drawLine(prev, p)
        prev = p
    painter.drawLine(prev, QPointF(x + c, y))
    # 四角括号
    painter.setPen(QPen(QColor(accent), 2))
    b = bracket
    for cx, cy, sx, sy in ((x, y, 1, 1), (x + w, y, -1, 1), (x, y + h, 1, -1), (x + w, y + h, -1, -1)):
        painter.drawLine(QPointF(cx, cy + b * sy), QPointF(cx, cy))
        painter.drawLine(QPointF(cx, cy), QPointF(cx + b * sx, cy))
    painter.restore()
    return path
