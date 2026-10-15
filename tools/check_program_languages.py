"""只检查语法编译，不导入 World、不执行运动。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sim.backend.core.program import parse, ProgramError


class ProgramLanguageTests(unittest.TestCase):
    def test_chinese_and_english_loops_have_identical_steps(self):
        cn = parse("速度 15\n循环 2\n等待 0.2\n结束", lambda _: None)
        en = parse("SPEED 15\nLOOP 2\nWAIT 0.2\nEND", lambda _: None)
        self.assertEqual(cn, en)
        self.assertEqual([step["kind"] for step in cn], ["speed", "wait", "wait"])

    def test_mixed_case_and_mixed_language_loop(self):
        result = parse("loop 2\n等待 0.2\nend", lambda _: None)
        self.assertEqual(len(result), 2)

    def test_chinese_tool_actions(self):
        for chinese, english in [("打开", "OPEN"), ("关闭", "CLOSE"), ("吸附", "VACUUM_ON"), ("释放", "VACUUM_OFF")]:
            self.assertEqual(parse("工具 " + chinese, lambda _: None), parse("TOOL " + english, lambda _: None))

    def test_invalid_end_is_rejected(self):
        for source in ["结束", "循环 2\n等待 0.2", "ENDless"]:
            with self.assertRaises(ProgramError):
                parse(source, lambda _: None)

    def test_nested_chinese_loop_is_rejected(self):
        with self.assertRaises(ProgramError):
            parse("循环 2\n循环 2\n等待 0.2\n结束\n结束", lambda _: None)


if __name__ == "__main__":
    unittest.main()
