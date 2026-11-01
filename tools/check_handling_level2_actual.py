from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
from _handling_advanced_actual import verify_course
class CourseTwo(unittest.IsolatedAsyncioTestCase):
    async def test_complete_second_course_actual_runner(self):await verify_course(self,2)
if __name__=='__main__':unittest.main()
