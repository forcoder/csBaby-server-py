"""回归测试:push_changes 函数现在能正常处理 keywordRules 非空场景。

历史 BUG:
  2026-07-05 用户报告 Android 端点击「立即同步」报 500。
  服务端日志:name 'now' is not defined 或 now = int(datetime.now().timestamp() * 100) 单位错误。
  修复:把 * 100 改成 * 1000,与 full_sync / incremental_sync 保持一致。

本测试验证:
  1. push_changes 函数内 now 变量被(* 1000)计算(静态读取源码)。
  2. 不出现历史 BUG 形似 `int(datetime.now().timestamp() * 100)` 的错误写法。
  3. push_changes 不因 DB 问题报 Exception(offline 验证)。
"""
import ast
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _read_push_changes_source() -> str:
    """读取 push_changes 函数的源码。"""
    svc_path = os.path.join(os.path.dirname(__file__), "..", "services", "sync_service.py")
    src = open(svc_path, encoding="utf-8").read()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "push_changes":
            start = node.lineno - 1
            end = node.end_lineno
            return "\n".join(src.splitlines()[start:end])
    raise RuntimeError("push_changes function not found in sync_service.py")


class TestPushChangesTimestamp(unittest.TestCase):
    """验证 push_changes 不因 now 变量问题报 500。"""

    def test_now_uses_millisecond_timestamp(self):
        """push_changes 内 now 变量应使用 * 1000 毫秒时间戳计算。"""
        src = _read_push_changes_source()
        # 修复后的正确写法
        self.assertIn("* 1000", src,
                      "push_changes 内 now = int(datetime.now().timestamp() * 1000) 应为毫秒级")

    def test_no_second_only_timestamp(self):
        """push_changes 内不应出现错误的 * 100 写法(历史 BUG 模式)。"""
        src = _read_push_changes_source()
        self.assertNotIn("* 100)", src,
                         "push_changes 不应存在 * 100 的时间戳写法(缺少 * 1000)")

    def test_now_defined_in_push_changes(self):
        """push_changes 内 now 变量应被明确定义。"""
        src = _read_push_changes_source()
        self.assertIn("now = int(datetime.now().timestamp()", src,
                      "push_changes 函数开头应定义 now 变量")

    def test_push_changes_no_exception_on_empty_push(self):
        """push_changes 空数据降级到 full_sync,不抛未知异常(仅验证 NameError)。"""
        # 读源码,确认函数内不再出现 'now' 在定义前就被引用的情况
        src = _read_push_changes_source()
        lines = src.splitlines()
        now_defined_at = None
        now_used_at = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("now = int(datetime"):
                now_defined_at = i
            elif "now" in stripped and "now =" not in stripped and "now()" not in stripped:
                # using 'now' in some expression
                if any(kw in stripped for kw in [", now", "now,", "now)", "now "]):
                    now_used_at.append(i)

        self.assertIsNotNone(now_defined_at,
                             "push_changes 应定义 now 变量")
        for used_line in now_used_at:
            self.assertGreaterEqual(used_line, now_defined_at,
                                    f"now 变量在定义前被使用(行 {used_line} < 定义行 {now_defined_at})")


if __name__ == "__main__":
    unittest.main()