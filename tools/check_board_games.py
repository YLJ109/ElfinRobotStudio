"""棋类 2D 规则离线回归：只测内存规则与 HTTP 契约，不碰机器人/相机/IO。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _board_game_cases import (  # noqa: E402
    ChessCases, FastReplyCases, GameAssetCases, GameRouteCases, GoCases, GomokuCases, LevelCases, TicTacToeCases, XiangqiCases,
)

if __name__ == '__main__':
    unittest.main()
