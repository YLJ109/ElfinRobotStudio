from _offline_guard import require_offline_copy
require_offline_copy()
import unittest
from _handling_advanced_actual import verify_course
class CourseThree(unittest.IsolatedAsyncioTestCase):
    async def test_complete_third_course_actual_runner(self):await verify_course(self,3)
if __name__=='__main__':unittest.main()
