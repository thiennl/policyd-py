import os
import tempfile
import unittest
from unittest import IsolatedAsyncioTestCase

from policyd_py.script_runner import ScriptRunner


class ScriptRunnerRenderTests(unittest.TestCase):
    def test_render_substitutes_variables(self):
        runner = ScriptRunner(timeout_seconds=5)
        result = runner._render("echo ${email} ${action}", {"email": "a@b.com", "action": "lock"})
        self.assertIn("a@b.com", result)
        self.assertIn("lock", result)

    def test_render_adds_timestamp(self):
        runner = ScriptRunner(timeout_seconds=5)
        result = runner._render("echo ${timestamp}", {})
        self.assertIn("T", result)

    def test_render_safe_substitute_ignores_missing_keys(self):
        runner = ScriptRunner(timeout_seconds=5)
        result = runner._render("echo ${missing}", {"other": "val"})
        self.assertEqual(result, "echo ${missing}")

    def test_to_string_handles_none(self):
        self.assertEqual(ScriptRunner._to_string(None), "")

    def test_to_string_handles_int(self):
        self.assertEqual(ScriptRunner._to_string(42), "42")


class ScriptRunnerRunTests(IsolatedAsyncioTestCase):
    async def test_successful_command(self):
        runner = ScriptRunner(timeout_seconds=5)
        output = await runner.run("echo ${value}", {"value": "hello"})
        self.assertEqual(output, "hello")

    async def test_non_zero_exit_raises(self):
        runner = ScriptRunner(timeout_seconds=5)
        with self.assertRaises(RuntimeError) as ctx:
            await runner.run("sh -c 'exit 1'", {})
        self.assertIn("exit code 1", str(ctx.exception))

    async def test_empty_argv_raises(self):
        runner = ScriptRunner(timeout_seconds=5)
        with self.assertRaises(RuntimeError):
            await runner.run("", {})

    async def test_timeout_raises(self):
        runner = ScriptRunner(timeout_seconds=1)
        with self.assertRaises(RuntimeError) as ctx:
            await runner.run("sleep 10", {})
        self.assertIn("timed out", str(ctx.exception))

    async def test_stderr_captured_on_failure(self):
        runner = ScriptRunner(timeout_seconds=5)
        with self.assertRaises(RuntimeError) as ctx:
            await runner.run("sh -c 'echo err_msg >&2; exit 1'", {})
        self.assertIn("err_msg", str(ctx.exception))
