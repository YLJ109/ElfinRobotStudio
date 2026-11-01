"""Focused diagnostic; formal coverage remains in check_board_robot.py."""
from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
import check_board_robot as cases

class VacuumEdges(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = cases.BoardRobotCases.asyncSetUp
    asyncTearDown = cases.BoardRobotCases.asyncTearDown
    pump = cases.BoardRobotCases.pump
    begin = cases.BoardRobotCases.begin
    test_go_and_gomoku_board_edges_and_both_supply_rows_execute = cases.BoardRobotCases.test_go_and_gomoku_board_edges_and_both_supply_rows_execute

if __name__ == '__main__':
    unittest.main()
