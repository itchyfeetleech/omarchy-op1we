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


if __name__ == "__main__":
    unittest.main()
