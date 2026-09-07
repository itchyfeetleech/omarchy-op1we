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

    def run_cli(self, argv, identity=IDENTITY):
        buf = io.StringIO()
        with mock.patch("op1we.device.discover", return_value=identity), \
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

    def apply_changes(self, changes):
        code, doc = self.run_cli(["read"])
        self.assertEqual(code, 0)
        req = {"apiVersion": 1, "expectedRevision": doc["data"]["revision"],
               "changes": changes}
        return self.run_cli_stdin(["apply"], json.dumps(req).encode())

    def test_reset_and_profile_cycle(self):
        self.run_cli(["enroll", "--confirm"])
        # Clear the fixture's unconfirmed specials first: a profile
        # holding them cannot round-trip through reset (F-001 rejects
        # unsupported diffs instead of replaying raw bytes).
        code, doc = self.apply_changes({"keys": [
            {"slot": 7, "action": {"kind": "unassigned"}},
            {"slot": 8, "action": {"kind": "unassigned"}},
        ]})
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["profile", "save", "--name", "orig"])
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["profile", "list"])
        self.assertEqual([p["name"] for p in doc["data"]["profiles"]], ["orig"])
        # Change something, then reset to documented defaults.
        code, doc = self.apply_changes({"debounceMs": 9})
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["reset"])
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["reset"])
        self.assertEqual(doc["data"]["snapshot"]["debounceMs"], 3)
        # Profile round-trips the pre-reset state back.
        code, doc = self.run_cli(["profile", "apply", "--name", "orig"])
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["restored"])
        code, doc = self.run_cli(["profile", "show", "--name", "orig"])
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["profile", "delete", "--name", "orig"])
        self.assertEqual(code, 0)


class LostAckTransport(FakeTransport):
    """Commits the first write, then loses its ACK."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sends = 0
        self.lost = False

    def exchange(self, opcode, payload=b"", timeout=2.0):
        from op1we import protocol as protocol_mod

        if opcode == protocol_mod.OP_EEPROM_WRITE:
            self.sends += 1
            if not self.lost:
                self.lost = True
                super().exchange(opcode, payload, timeout)
                from op1we import device as device_mod

                raise device_mod.Op1weError("timeout", "lost ack", retryable=True)
        return super().exchange(opcode, payload, timeout)


class FailureEnvelopeTest(unittest.TestCase):
    """F-002/F-009: failures carry one JSON envelope with recovery detail."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")
        self.fake = FakeTransport(IDENTITY, mem=load_eeprom())

    def run_cli(self, argv, identity=IDENTITY):
        buf = io.StringIO()
        with mock.patch("op1we.device.discover", return_value=identity), \
             mock.patch("op1we.controller.real_controller",
                        return_value=__import__("op1we.controller", fromlist=["Controller"]).Controller(
                            lambda ident: self.fake)), \
             redirect_stdout(buf):
            code = cli.main(argv)
        out = buf.getvalue().strip().splitlines()
        self.assertEqual(len(out), 1, f"stdout must be exactly one JSON line: {out!r}")
        return code, json.loads(out[0])

    def run_cli_stdin(self, argv, stdin_bytes):
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
        return code, json.loads(out[0])

    def test_apply_failure_carries_recovery_detail(self):
        self.run_cli(["enroll", "--confirm"])
        code, doc = self.run_cli(["read"])
        revision = doc["data"]["revision"]
        flaky = LostAckTransport(IDENTITY, mem=dict(self.fake.mem))
        self.fake = flaky
        req = {"apiVersion": 1, "expectedRevision": revision,
               "changes": {"debounceMs": 7}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 5)
        self.assertEqual(doc["error"]["code"], "write-failed")
        self.assertEqual(flaky.sends, 1)
        self.assertTrue(os.path.exists(doc["error"]["detail"]["backup"]))

    def test_profile_missing_args_are_envelopes(self):
        for argv in (["profile", "import"],
                    ["profile", "export", "--name", "x"],
                    ["profile", "save"],
                    ["profile", "show"],
                    ["profile", "delete"],
                    ["profile", "apply"]):
            code, doc = self.run_cli(argv)
            self.assertEqual(code, 2, argv)
            self.assertFalse(doc["ok"])
            self.assertEqual(doc["error"]["code"], "invalid-input")
        self.assertEqual(self.fake.writes, [])

    def test_status_reports_freshness(self):
        code, doc = self.run_cli(["status"])
        self.assertEqual(code, 0)
        self.assertTrue(doc["data"]["batteryFresh"])

    def test_apply_noop_revision_checked(self):
        self.run_cli(["enroll", "--confirm"])
        req = {"apiVersion": 1, "expectedRevision": "0" * 32, "changes": {}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 4)
        self.assertEqual(doc["error"]["code"], "stale-revision")
        code, doc = self.run_cli(["read"])
        req = {"apiVersion": 1, "expectedRevision": doc["data"]["revision"],
               "changes": {}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 0)
        self.assertFalse(doc["data"]["applied"])

    def test_apply_without_revision_rejected(self):
        self.run_cli(["enroll", "--confirm"])
        req = {"apiVersion": 1, "changes": {"debounceMs": 2}}
        code, doc = self.run_cli_stdin(["apply"], json.dumps(req).encode())
        self.assertEqual(code, 2)
        self.assertEqual(doc["error"]["code"], "invalid-input")
        self.assertEqual(self.fake.writes, [])

    def _write_backup(self, mem):
        path = os.path.join(self.tmp.name, "crafted.json")
        with open(path, "w") as handle:
            json.dump({"apiVersion": 1, "kind": "op1we-backup",
                       "fingerprint": IDENTITY.fingerprint,
                       "bytes": {f"{addr:04x}": value for addr, value in mem.items()}},
                      handle)
        return path

    def test_restore_rejects_unsupported_target(self):
        self.run_cli(["enroll", "--confirm"])
        target = dict(load_eeprom())
        special8 = bytes([0x08, 0x00, 0x00, (0x55 - 0x08) & 0xFF])
        for k, byte in enumerate(special8):
            target[0x88 + k] = byte
        code, doc = self.run_cli(["restore", "--file", self._write_backup(target)])
        self.assertEqual(code, 3)
        self.assertEqual(doc["error"]["code"], "unsupported")
        self.assertEqual(self.fake.writes, [])

    def test_restore_unknown_only_is_noop(self):
        self.run_cli(["enroll", "--confirm"])
        code, doc = self.run_cli(
            ["restore", "--file", self._write_backup({0xA0: 0x99})])
        self.assertEqual(code, 0)
        self.assertFalse(doc["data"]["restored"])
        self.assertEqual(self.fake.writes, [])

    def test_replug_elsewhere_blocks_writes_until_reenroll(self):
        from op1we import device as device_mod

        self.run_cli(["enroll", "--confirm"])
        code, doc = self.run_cli(["backup"])
        path = doc["data"]["path"]
        moved = device_mod.DeviceIdentity(
            vid=0x3367, pid=0x1961, bcd_device="0101",
            manufacturer="Endgame Gear", product="x",
            usb_path="1-8:1.1", descriptor_sha256="ab" * 32,
            hidraw="/dev/hidraw3",
        )
        code, doc = self.run_cli(["restore", "--file", path], identity=moved)
        self.assertEqual(code, 3)
        self.assertEqual(doc["error"]["code"], "device-changed")
        self.assertEqual(self.fake.writes, [])
        code, doc = self.run_cli(["enroll", "--confirm"], identity=moved)
        self.assertEqual(code, 0)
        code, doc = self.run_cli(["restore", "--file", path], identity=moved)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
