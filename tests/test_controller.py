"""Controller tests over a fake transport: no hardware, no root."""

import json
import os
import tempfile
import unittest

from op1we import controller as controller_mod
from op1we import device as device_mod
from op1we import protocol
from test_protocol import load_eeprom

IDENTITY = device_mod.DeviceIdentity(
    vid=0x3367,
    pid=0x1961,
    bcd_device="0101",
    manufacturer="Endgame Gear",
    product="Endgame Gear WE Series Gaming Receiver",
    usb_path="1-7:1.1",
    descriptor_sha256="ab" * 32,
    hidraw="/dev/hidraw9",
)


def reply(opcode, data=b"", addr=0):
    buf = bytearray(17)
    buf[0] = 0x09
    buf[1] = opcode
    buf[3] = (addr >> 8) & 0xFF
    buf[4] = addr & 0xFF
    buf[5] = len(data)
    buf[6:6 + len(data)] = data
    buf[16] = (0x55 - sum(buf[:16])) & 0xFF
    return bytes(buf)


class FakeTransport:
    """Scriptable stand-in for HidrawTransport."""

    def __init__(self, identity, mem=None, battery=(70, 0), link=True, profile=1,
                 asleep=False, dead=False, notifications=()):
        self.identity = identity
        self.mem = dict(mem) if mem is not None else load_eeprom()
        self.battery = battery
        self.link = link
        self.profile = profile
        self.asleep = asleep
        self.dead = dead
        self.notifications = list(notifications)
        self.writes = []  # (addr, data) actually applied

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return None

    def exchange(self, opcode, payload=b"", timeout=2.0):
        if opcode not in protocol.ALLOWED_OPCODES:
            raise AssertionError(f"allowlist bypass attempt: 0x{opcode:02x}")
        if self.dead:
            raise device_mod.Op1weError("unavailable", "dead", retryable=True)
        if opcode == protocol.OP_BATTERY:
            if self.battery is None:
                raise device_mod.Op1weError("timeout", "no battery", retryable=True)
            return reply(opcode, bytes(self.battery))
        if opcode == protocol.OP_LINK:
            return reply(opcode, bytes([1 if self.link else 0]))
        if opcode == protocol.OP_PROFILE:
            return reply(opcode, bytes([self.profile]))
        if self.asleep and opcode in (protocol.OP_EEPROM_READ, protocol.OP_EEPROM_WRITE):
            raise device_mod.Op1weError("timeout", "asleep", retryable=True)
        if opcode == protocol.OP_EEPROM_READ:
            addr = (payload[1] << 8) | payload[2]
            length = payload[3]
            data = bytes(self.mem.get(addr + k, 0xFF) for k in range(length))
            return reply(opcode, data, addr)
        if opcode == protocol.OP_EEPROM_WRITE:
            addr = (payload[1] << 8) | payload[2]
            length = payload[3]
            data = bytes(payload[4:4 + length])
            for k, byte in enumerate(data):
                self.mem[addr + k] = byte
            self.writes.append((addr, data))
            return reply(opcode, data, addr)
        raise AssertionError("unreachable")


class IsolatedState(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")


class StatusTest(IsolatedState):
    def test_connected(self):
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident))
        status = ctl.status(IDENTITY)
        self.assertEqual(status.connection, "connected")
        self.assertEqual(status.percent, 70)
        self.assertEqual(status.charging, 0)
        self.assertEqual(status.profile, 1)
        self.assertTrue(status.link_up)
        self.assertTrue(status.battery_fresh)

    def test_receiver_only_when_link_down(self):
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident, link=False))
        status = ctl.status(IDENTITY)
        self.assertEqual(status.connection, "receiver-only")
        self.assertEqual(status.percent, 70)  # cached battery still reported
        self.assertFalse(status.battery_fresh)  # ...but flagged stale

    def test_unavailable_when_dead(self):
        ctl = controller_mod.Controller(
            lambda ident: FakeTransport(ident, battery=None, link=False, dead=True)
        )
        status = ctl.status(IDENTITY)
        self.assertEqual(status.connection, "unavailable")
        self.assertIsNone(status.percent)


class ReadTest(IsolatedState):
    def test_snapshot_decodes_fixture(self):
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident))
        snap = ctl.snapshot(IDENTITY)
        self.assertEqual(snap.polling_hz, 1000)
        self.assertEqual(snap.debounce_ms, 1)
        self.assertEqual(snap.profile, 1)
        self.assertEqual([(s.x, s.y) for s in snap.stages],
                         [(400, 400), (800, 800), (1600, 1600), (3200, 3200)])
        self.assertEqual(snap.raw[0xA9], 0x01)

    def test_asleep_is_distinct(self):
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident, asleep=True))
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.read_memory(IDENTITY, [(0x00, 0x09)], timeout=0.2)
        self.assertEqual(ctx.exception.code, "asleep")


class ApplyTest(IsolatedState):
    def test_debounce_write_verifies(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        before = ctl.read_memory(IDENTITY)
        revision = protocol.config_revision(before)
        snap = ctl.apply_bytes(
            IDENTITY, {0xA9: bytes([0x02, 0x53])}, expected_revision=revision
        )
        self.assertEqual(snap.debounce_ms, 2)
        self.assertEqual(fake.writes, [(0xA9, bytes([0x02, 0x53]))])
        # Unknown bytes elsewhere survive the edit.
        self.assertEqual(snap.raw[0x00], before[0x00])
        self.assertEqual(snap.raw[0x60], before[0x60])

    def test_stale_revision_writes_nothing(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply_bytes(IDENTITY, {0xA9: bytes([0x02, 0x53])}, expected_revision="0" * 32)
        self.assertEqual(ctx.exception.code, "stale-revision")
        self.assertEqual(fake.writes, [])

    def test_out_of_region_writes_nothing(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(ValueError):
            ctl.apply_bytes(IDENTITY, {0xB5: b"\x00"})
        self.assertEqual(fake.writes, [])


class BackupTest(IsolatedState):
    def test_backup_round_trip_and_permissions(self):
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident))
        mem = ctl.read_memory(IDENTITY)
        path = ctl.write_backup_file(IDENTITY, mem)
        self.assertTrue(path.endswith(".json"))
        self.assertEqual(oct(os.stat(path).st_mode & 0o777), "0o600")
        loaded = ctl.load_backup_file(path)
        self.assertEqual(loaded, mem)

    def test_backup_prunes_to_latest_10(self):
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident))
        mem = ctl.read_memory(IDENTITY)
        for _ in range(12):
            ctl.write_backup_file(IDENTITY, mem)
        names = [n for n in os.listdir(os.environ["OP1WE_STATE_DIR"]) if n.startswith("backup-")]
        self.assertEqual(len(names), 10)

    def test_corrupt_backup_rejected(self):
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident))
        bad = os.path.join(self.tmp.name, "bad.json")
        with open(bad, "w") as handle:
            json.dump({"kind": "nope"}, handle)
        with self.assertRaises(device_mod.Op1weError):
            ctl.load_backup_file(bad)


class LockTest(IsolatedState):
    def test_second_lock_is_busy(self):
        with device_mod.DeviceLock(IDENTITY):
            with self.assertRaises(device_mod.Op1weError) as ctx:
                with device_mod.DeviceLock(IDENTITY):
                    pass
            self.assertEqual(ctx.exception.code, "busy")


class CountingProxy:
    """Transport factory wrapper counting lock sessions (F-003)."""

    def __init__(self, inner, counter):
        self.inner = inner
        self.counter = counter

    def __call__(self, identity):
        self.counter[0] += 1
        return self.inner


class AtomicReadTest(IsolatedState):
    def test_snapshot_reads_under_one_session(self):
        fake = FakeTransport(IDENTITY)
        counter = [0]
        ctl = controller_mod.Controller(CountingProxy(fake, counter))
        ctl.snapshot(IDENTITY)
        self.assertEqual(counter[0], 1)

    def test_backup_reads_payloads_under_one_session(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        ctl.apply(IDENTITY, {"keys": [{"slot": 11, "action": {"kind": "key", "keys": [0x04]}}]},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        counter = [0]
        ctl2 = controller_mod.Controller(CountingProxy(fake, counter))
        mem = ctl2.read_full_backup(IDENTITY)
        self.assertEqual(counter[0], 1)
        base = protocol.type5_addr(11)
        self.assertEqual(mem[base], 2)  # payload event count present


class RecoveryBackupTest(IsolatedState):
    def test_auto_backup_captures_active_payloads(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        ctl.apply(IDENTITY, {"keys": [{"slot": 4, "action": {"kind": "key", "keys": [0x06]}}]},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        ctl.apply(IDENTITY, {"debounceMs": 5},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        state = os.environ["OP1WE_STATE_DIR"]
        backups = [os.path.join(state, n) for n in os.listdir(state)
                   if n.startswith("backup-")]
        self.assertTrue(backups)
        latest = max(backups, key=controller_mod.Controller._backup_seq)
        with open(latest) as handle:
            payload = json.load(handle)
        base = protocol.type5_addr(4)
        slot = bytes(payload["bytes"][f"{base + k:04x}"] for k in range(32))
        self.assertEqual(protocol.parse_type5_payload(slot)["keys"], [0x06])

    def test_recovery_backup_restores_bindings(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        ctl.apply(IDENTITY, {"keys": [{"slot": 4, "action": {"kind": "key", "keys": [0x06]}}]},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        backup = ctl.read_full_backup(IDENTITY)
        ctl.apply(IDENTITY, {"keys": [{"slot": 4, "action": {"kind": "key", "keys": [0x07]}}]},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        snap, chunks, changed = ctl.restore_mem(IDENTITY, backup)
        self.assertTrue(changed)
        base = protocol.type5_addr(4)
        slot = bytes(fake.mem.get(base + k, 0xFF) for k in range(32))
        self.assertEqual(protocol.parse_type5_payload(slot)["keys"], [0x06])


class PruneBurstTest(IsolatedState):
    def test_same_second_burst_keeps_newest(self):
        from unittest import mock
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident))
        with mock.patch("time.strftime", return_value="20260907T120000"):
            paths = [ctl.write_backup_file(IDENTITY, {0: i}) for i in range(12)]
        for path in paths[-10:]:
            self.assertTrue(os.path.exists(path), path)
        state = os.environ["OP1WE_STATE_DIR"]
        kept = [n for n in os.listdir(state) if n.startswith("backup-")]
        self.assertEqual(len(kept), 10)
        contents = []
        for name in kept:
            with open(os.path.join(state, name)) as handle:
                contents.append(json.load(handle)["bytes"]["0000"])
        self.assertEqual(sorted(contents), list(range(2, 12)))


class MalformedReplyTest(IsolatedState):
    def test_malformed_battery_degrades_to_unknown(self):
        ctl = controller_mod.Controller(
            lambda ident: FakeTransport(ident, battery=(101, 0)))
        status = ctl.status(IDENTITY)
        self.assertIsNone(status.percent)
        self.assertFalse(status.battery_fresh)
        self.assertEqual(status.connection, "connected")  # link still up

    def test_malformed_link_degrades_to_unknown(self):
        class BadLink(FakeTransport):
            def exchange(self, opcode, payload=b"", timeout=2.0):
                if opcode == protocol.OP_LINK:
                    return reply(opcode, bytes([7]))
                return super().exchange(opcode, payload, timeout)

        ctl = controller_mod.Controller(lambda ident: BadLink(ident))
        status = ctl.status(IDENTITY)
        self.assertIsNone(status.link_up)
        self.assertEqual(status.connection, "receiver-only")
        self.assertFalse(status.battery_fresh)

    def test_missing_charging_byte_is_unknown(self):
        ctl = controller_mod.Controller(
            lambda ident: FakeTransport(ident, battery=(70,)))
        status = ctl.status(IDENTITY)
        self.assertEqual(status.percent, 70)
        self.assertIsNone(status.charging)
        self.assertTrue(status.battery_fresh)

    def test_corrupt_verify_echo_is_verify_failed(self):
        class BadEcho(FakeTransport):
            def exchange(self, opcode, payload=b"", timeout=2.0):
                out = super().exchange(opcode, payload, timeout)
                if (opcode == protocol.OP_EEPROM_READ and len(payload) >= 4
                        and payload[3] == 2
                        and (payload[1] << 8 | payload[2]) == protocol.ADDR_DEBOUNCE):
                    mut = bytearray(out)
                    mut[4] ^= 0xFF
                    mut[16] = (0x55 - sum(mut[:16])) & 0xFF
                    return bytes(mut)
                return out

        fake = BadEcho(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply_bytes(IDENTITY, {protocol.ADDR_DEBOUNCE: bytes([2, 0x53])},
                            timeout=0.5)
        self.assertEqual(ctx.exception.code, "verify-failed")
        self.assertTrue(os.path.exists(ctx.exception.detail["backup"]))

    def test_state_dir_failure_is_envelope_error(self):
        blocker = os.path.join(self.tmp.name, "afile")
        with open(blocker, "w") as handle:
            handle.write("x")
        os.environ["OP1WE_STATE_DIR"] = blocker
        ctl = controller_mod.Controller(lambda ident: FakeTransport(ident))
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.write_backup_file(IDENTITY, {0: 1})
        self.assertEqual(ctx.exception.code, "unavailable")


class BudgetTest(IsolatedState):
    def test_read_budget_binds_whole_operation(self):
        import time as time_mod

        class SlowFake(FakeTransport):
            def exchange(self, opcode, payload=b"", timeout=2.0):
                if opcode == protocol.OP_EEPROM_READ:
                    time_mod.sleep(0.15)
                return super().exchange(opcode, payload, timeout)

        ctl = controller_mod.Controller(lambda ident: SlowFake(ident))
        start = time_mod.monotonic()
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.read_memory(IDENTITY, timeout=1.0)
        elapsed = time_mod.monotonic() - start
        # 19 chunks x 0.15 s = 2.85 s unbudgeted; per-chunk renewal
        # would have succeeded slowly instead of failing fast.
        self.assertEqual(ctx.exception.code, "asleep")
        self.assertLess(elapsed, 2.0)

    def test_sufficient_budget_reads_fully(self):
        import time as time_mod

        class SlowFake(FakeTransport):
            def exchange(self, opcode, payload=b"", timeout=2.0):
                if opcode == protocol.OP_EEPROM_READ:
                    time_mod.sleep(0.15)
                return super().exchange(opcode, payload, timeout)

        ctl = controller_mod.Controller(lambda ident: SlowFake(ident))
        mem = ctl.read_memory(IDENTITY, timeout=5.0)
        self.assertEqual(len(mem), 0xB5)


class EnrollmentTest(IsolatedState):
    def moved_identity(self):
        return device_mod.DeviceIdentity(
            vid=0x3367, pid=0x1961, bcd_device="0101",
            manufacturer="Endgame Gear", product="x",
            usb_path="1-8:1.1", descriptor_sha256="ab" * 32,
            hidraw="/dev/hidraw3",
        )

    def test_usb_path_continuity(self):
        controller_mod.save_enrollment(IDENTITY)
        self.assertTrue(controller_mod.is_enrolled(IDENTITY))
        moved = self.moved_identity()
        self.assertFalse(controller_mod.is_enrolled(moved))
        with self.assertRaises(device_mod.Op1weError) as ctx:
            controller_mod.require_enrolled(moved)
        self.assertEqual(ctx.exception.code, "device-changed")
        controller_mod.save_enrollment(moved)  # re-confirm after checking
        self.assertTrue(controller_mod.is_enrolled(moved))


if __name__ == "__main__":
    unittest.main()
