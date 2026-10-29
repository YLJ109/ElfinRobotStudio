"""有界数据复杂度回归：小图模拟大量展开，不创建大字符串。"""
from _offline_guard import require_offline_copy
require_offline_copy()

import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.core import python_program as module
from backend.core.python_program import Interpreter
from backend.core.program import ProgramError


def interpreter(source='WAIT(0)'):
    return Interpreter({'source': source, 'points': [], 'params': {}})


class PythonDataBudgetTests(unittest.TestCase):
    def test_sharing_counts_expanded_data_not_unique_objects(self):
        value = ['x' * 1000] * 200
        with self.assertRaises(ValueError): interpreter().guard(value)

    def test_shared_dag_budget_does_not_expand_exponentially(self):
        value = [1]
        for _ in range(24): value = [value, value]
        with self.assertRaises(ValueError): interpreter().guard(value)

    def test_cycles_and_excessive_depth_are_explicitly_rejected(self):
        cycle = []; cycle.append(cycle)
        deep = 1
        for _ in range(40): deep = [deep]
        for value in (cycle, deep):
            with self.subTest(kind='cycle' if value is cycle else 'deep'):
                with self.assertRaises(ValueError): interpreter().guard(value)

    def test_kwargs_are_checked_before_native_builtin(self):
        run = interpreter('min([], default=a)')
        run.scope['a'] = ['x' * 1000] * 200
        native = Mock(return_value=0)
        with patch.object(module, 'min', native, create=True):
            with self.assertRaises(ProgramError): list(run.run())
        native.assert_not_called()

    def test_comparison_is_checked_before_native_operator(self):
        run = interpreter('a == b')
        run.scope.update(a=['x' * 1000] * 200, b=[])
        native = Mock(return_value=False)
        with patch.object(module.operator, 'eq', native):
            with self.assertRaises(ProgramError): list(run.run())
        native.assert_not_called()

    def test_str_is_rejected_before_conversion_not_after_allocation(self):
        run = interpreter('str(a)')
        run.scope['a'] = ['x' * 1000] * 200
        with self.assertRaises(ProgramError): list(run.run())

    def test_mutating_methods_cannot_create_cycles(self):
        for source in ('a=[]\na.append(a)', 'a={}\na.update(self=a)',
                       'a=[]\nb=[a]\na.append(b)'):
            run = interpreter(source)
            with self.assertRaises(ProgramError): list(run.run())
            self.assertEqual(run.scope['a'], {} if 'a={}' in source else [])

    def test_subscript_and_slice_cannot_create_cycles(self):
        for source in ('a=[0]\na[0]=a', 'a=[0]\na[:]=[a]', 'a={}\na["self"]=a'):
            run = interpreter(source)
            with self.assertRaises(ProgramError): list(run.run())
            self.assertEqual(run.scope['a'], {} if 'a={}' in source else [0])

    def test_repetition_budget_is_checked_before_native_multiply(self):
        run = interpreter()
        native = Mock(return_value=[])
        with patch.object(module.operator, 'mul', native):
            with self.assertRaises(ValueError): run.binary(ast.Mult(), ['x' * 1000], 200)
        native.assert_not_called()

    def test_membership_work_budget_precedes_native_scan(self):
        run = interpreter('needle in values')
        # 每个值都不超展开预算，但相乘后的比较工作量明显过大。
        run.scope.update(needle=['x' * 1000] * 20,
                         values=[['x' * 1000] * 20] * 3)
        with self.assertRaises(ProgramError): list(run.run())

    def test_legitimate_data_and_user_functions_remain_usable(self):
        run = interpreter('def f(x):\n    return x+1\na=[1,2]\na.append(3)\n'
                          'a[0]=4\na[1:]=[5,6]\nd={"v":2}\nd.update(v=3)\n'
                          's=str(a)\nn=min(a, default=0)\nok=[1,2] in [[1,2]]\nWAIT(f(d.get("v"))/100)')
        steps = [step for step in run.run() if step['kind'] != 'script_yield']
        self.assertEqual(steps[-1]['sec'], .04)
        self.assertEqual(run.scope['a'], [4,5,6])
        self.assertEqual(run.scope['s'], '[4, 5, 6]')
        self.assertTrue(run.scope['ok'])

    def test_existing_single_container_limits_remain_valid(self):
        run = interpreter()
        for value in ('x' * 10000, list(range(10000)), dict(zip(range(10000), range(10000)))):
            self.assertIs(run.guard(value), value)


if __name__ == '__main__':
    unittest.main()
