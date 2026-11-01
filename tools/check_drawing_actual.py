"""Complete six-shape execution in the user's unchanged front range."""
from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
import check_drawing as fixtures

class ActualDrawing(unittest.IsolatedAsyncioTestCase):
    setUp=fixtures.DrawingTests.setUp
    asyncTearDown=fixtures.DrawingTests.asyncTearDown
    ready=fixtures.DrawingTests.ready
    pump=fixtures.DrawingTests.pump
    test_user_front_range_is_unchanged_and_large_six_shapes_execute_at_checked_tool_tip=fixtures.DrawingTests.test_user_front_range_is_unchanged_and_large_six_shapes_execute_at_checked_tool_tip
    test_front_facing_pen_axis_and_large_heart_actual_execution=fixtures.DrawingTests.test_front_facing_pen_axis_and_large_heart_actual_execution

if __name__=='__main__':unittest.main()
