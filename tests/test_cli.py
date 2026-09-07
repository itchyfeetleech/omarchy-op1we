"""CLI envelope tests: JSON-only stdout, exit codes, no hardware."""

import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

from op1we import __main__ as cli
from test_controller import IDENTITY, FakeTransport, load_eeprom


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")
        self.fake = FakeTransport(IDENTITY, mem=load_eeprom())

    def run_cli(self, argv):
        buf = io.StringIO()
        with mock.patch("op1we.device.discover", return_value=IDENTITY), \
             mock.patch("op1we.controller.real_controller",
                        return_value=__import__("op1we.controller", fromlist=["Controller"]).Controller(
                            lambda ident: self.fake)), \
             redirect_stdout(buf):
            code = cli.main(argv)
        out = buf.getvalue().strip().splitlines()
        self.assertEqual(len(out), 1, f"stdout must be exactly one JSON line: {out!r}")
        return code, json.loads(out[0])

    def test_status_envelope(self):
        code, doc = self.run_cli(["status", "--request-id", "abc"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["apiVersion"], 1)
        self.assertEqual(doc["requestId"], "abc")
        self.assertTrue(doc["ok"])
        self.assertEqual(doc["data"]["batteryPercent"], 70)
        self.assertIsNone(doc["error"])

    def test_read_envelope(self):
        code, doc = self.run_cli(["read"])
        self.assertEqual(code, 0)
        self.assertEqual(doc["data"]["pollingHz"], 1000)
        self.assertEqual(len(doc["data"]["stages"]), 4)

    def test_enroll_requires_confirm(self):
        code, doc = self.run_cli(["enroll"])
        self.assertEqual(code, 2)
        self.assertFalse(doc["ok"])
        self.assertEqual(doc["error"]["code"], "invalid-input")

    def test_enroll_and_restore_round_trip(self):
        code, doc = self.run_cli(["enroll", "--confirm"])
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["backup"])
        self.assertEqual(code, 0)
        path = doc["data"]["path"]
        # Tamper the live device, then restore.
        self.fake.mem[0xA9] = 0x05
        code, doc = self.run_cli(["restore", "--file", path])
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["restored"])
        self.assertEqual(self.fake.mem[0xA9], 0x01)

    def test_restore_without_enrollment_refused(self):
        code, doc = self.run_cli(["backup"])
        path = doc["data"]["path"]
        code, doc = self.run_cli(["restore", "--file", path])
        self.assertEqual(code, 3)
        self.assertEqual(doc["error"]["code"], "not-enrolled")

    def run_cli_stdin(self, argv, stdin_bytes):
        import io
        import json as json_mod
        buf = io.StringIO()
        fake_stdin = io.TextIOWrapper(io.BytesIO(stdin_bytes), encoding="utf-8")

        class StdinProxy:
            buffer = fake_stdin.buffer

        with mock.patch("op1we.device.discover", return_value=IDENTITY), \
             mock.patch("op1we.controller.real_controller",
                        return_value=__import__("op1we.controller", fromlist=["Controller"]).Controller(
                            lambda ident: self.fake)), \
             mock.patch.object(cli.sys, "stdin", StdinProxy()), \
             redirect_stdout(buf):
            code = cli.main(argv)
        out = buf.getvalue().strip().splitlines()
        self.assertEqual(len(out), 1)
        return code, json_mod.loads(out[0])

    def test_apply_stdin_round_trip(self):
        self.run_cli(["enroll", "--confirm"])
        revision = None
        code, doc = self.run_cli(["read"])
        revision = doc["data"]["revision"]
        req = {"apiVersion": 1, "expectedRevision": revision,
               "changes": {"debounceMs": 7, "pollingHz": 250}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["applied"])
        self.assertEqual(doc["data"]["snapshot"]["debounceMs"], 7)
        self.assertEqual(doc["data"]["snapshot"]["pollingHz"], 250)

    def test_apply_rejects_bad_stdin(self):
        self.run_cli(["enroll", "--confirm"])
        code, doc = self.run_cli_stdin(["apply"], b"not json")
        self.assertEqual(code, 2)
        code, doc = self.run_cli_stdin(["apply"], b"x" * (cli.STDIN_MAX_BYTES + 1))
        self.assertEqual(code, 2)
        code, doc = self.run_cli_stdin(
            ["apply"], json.dumps({"apiVersion": 99, "changes": {}}).encode())
        self.assertEqual(code, 2)
        # Zero writes for all of the above.
        self.assertEqual(self.fake.writes, [])

    def test_apply_rejects_stale_and_mismatch(self):
        self.run_cli(["enroll", "--confirm"])
        req = {"apiVersion": 1, "expectedRevision": "0" * 32,
               "changes": {"debounceMs": 2}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 4)
        req = {"apiVersion": 1, "deviceFingerprint": "other",
               "changes": {"debounceMs": 2}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 2)
        self.assertEqual(self.fake.writes, [])

    def test_reset_and_profile_cycle(self):
        self.run_cli(["enroll", "--confirm"])
        code, doc = self.run_cli(["profile", "save", "--name", "orig"])
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["profile", "list"])
        self.assertEqual([p["name"] for p in doc["data"]["profiles"]], ["orig"])
        # Change something, then reset to documented defaults.
        req = {"apiVersion": 1, "changes": {"debounceMs": 9}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["reset"])
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["reset"])
        self.assertEqual(doc["data"]["snapshot"]["debounceMs"], 3)
        # Profile round-trips the pre-reset state back.
        code, doc = self.run_cli(["profile", "apply", "--name", "orig"])
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["restored"])
        code, doc = self.run_cli(["profile", "delete", "--name", "orig"])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
