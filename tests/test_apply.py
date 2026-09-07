"""Milestone-2 apply/reset/profile/listen tests over fake transports."""

import os
import tempfile
import unittest

from op1we import controller as controller_mod
from op1we import device as device_mod
from op1we import protocol
from test_controller import IDENTITY, FakeTransport, load_eeprom


class FlakyTransport(FakeTransport):
    """Fault injection: drop N writes, corrupt readbacks, go dead."""

    def __init__(self, *args, drop_writes=0, drop_all_writes=False,
                 corrupt_readback=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.drop_writes = drop_writes
        self.drop_all_writes = drop_all_writes
        self.corrupt_readback = corrupt_readback

    def exchange(self, opcode, payload=b"", timeout=2.0):
        if opcode == protocol.OP_EEPROM_WRITE and (
                self.drop_all_writes or self.drop_writes > 0):
            self.drop_writes = max(0, self.drop_writes - 1)
            raise device_mod.Op1weError("timeout", "dropped", retryable=True)
        reply = super().exchange(opcode, payload, timeout)
        if (self.corrupt_readback and opcode == protocol.OP_EEPROM_READ
                and len(payload) >= 4 and payload[3] == 2
                and (payload[1] << 8 | payload[2]) == protocol.ADDR_DEBOUNCE):
            mut = bytearray(reply)
            mut[6] ^= 0xFF
            mut[16] = (0x55 - sum(mut[:16])) & 0xFF
            return bytes(mut)
        return reply


class ApplyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")

    def fresh_revision(self, ctl):
        return ctl.snapshot(IDENTITY).revision

    def test_model_mismatch_or_failed_query_never_writes(self):
        from test_controller import reply
        for mode in ("xm2we", "unknown", "malformed", "timeout"):
            with self.subTest(mode=mode):
                class WrongModel(FakeTransport):
                    def exchange(self, opcode, payload=b"", timeout=2.0):
                        if opcode == protocol.OP_MODEL:
                            if mode == "timeout":
                                raise device_mod.Op1weError("timeout", "no model reply")
                            if mode == "malformed":
                                return reply(opcode, b"\x35\x02")
                            mid = 1 if mode == "xm2we" else 99
                            return reply(opcode, bytes([0x35, mid, 0, 0, 0x35, mid, 0, 0]))
                        return super().exchange(opcode, payload, timeout)
                fake = WrongModel(IDENTITY)
                ctl = controller_mod.Controller(lambda ident: fake)
                with self.assertRaises(device_mod.Op1weError):
                    ctl.apply_bytes(IDENTITY, {protocol.ADDR_DEBOUNCE: bytes([2, 0x53])})
                self.assertEqual(fake.writes, [])

    def test_full_apply(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        snap, chunks = ctl.apply(IDENTITY, {
            "pollingHz": 500,
            "cpi": [400, 800, None, 1600],
            "debounceMs": 5,
            "sleepS": 120,
            "ripple": True,
            "fixline": True,
            "turnOffLight": False,
            "keys": [{"slot": 12, "action": {"kind": "mouse", "buttons": ["left"]}}],
        }, expected_revision=self.fresh_revision(ctl))
        self.assertGreater(chunks, 0)
        self.assertEqual(snap.polling_hz, 500)
        self.assertEqual(snap.debounce_ms, 5)
        self.assertEqual(snap.sleep_s, 120)
        self.assertTrue(snap.ripple)
        self.assertTrue(snap.fixline)
        self.assertFalse(snap.turn_off_light)
        self.assertEqual(fake.mem[0x00], 0x02)
        self.assertEqual([(s.x, s.y) for s in snap.stages],
                         [(400, 400), (800, 800), (1600, 1600), (1600, 1600)])
        # Unknown bytes survive the edit.
        before = load_eeprom()
        for addr in (0xA0, 0xA1, 0xA6, 0xA7, 0xA8, 0xAB, 0x02, 0x04, 0x06, 0x08, 0x0A):
            self.assertEqual(fake.mem[addr], before[addr], f"0x{addr:02x}")

    def test_key_and_media_bind(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        snap, _ = ctl.apply(IDENTITY, {"keys": [
            {"slot": 11, "action": {"kind": "key", "keys": [0x04]}},
            {"slot": 12, "action": {"kind": "media", "usage": "play-pause"}},
        ]}, expected_revision=self.fresh_revision(ctl))
        bindings = {b.slot: b for b in snap.bindings}
        # Snapshot re-reads payloads only for key-ref slots in config mem.
        self.assertEqual(bindings[11].action["kind"], "key-ref")
        self.assertEqual(bindings[12].action["kind"], "key-ref")
        payload_addr = protocol.type5_addr(11)
        count = fake.mem[payload_addr]
        self.assertEqual(count, 2)
        media_addr = protocol.type5_addr(12)
        self.assertEqual(fake.mem[media_addr + 1], protocol.EV_MEDIA_DOWN)

    def test_invalid_plan_writes_nothing(self):
        for changes in ({"pollingHz": 2000},
                        {"cpi": [25]},
                        {"cpi": [10100]},
                        {"debounceMs": 31},
                        {"sleepS": 61},
                        {"ripple": "yes"},
                        {"keys": [{"slot": 1, "action": {"kind": "mouse", "buttons": ["nope"]}}]},
                        {"keys": [{"slot": 13, "action": {"kind": "key", "keys": [4]}}]},
                        {"keys": [{"slot": 1, "action": {"kind": "media", "usage": 1}}]},
                        {"lod": 2},
                        {"keys": "nope"}):
            fake = FakeTransport(IDENTITY)
            ctl = controller_mod.Controller(lambda ident: fake)
            with self.assertRaises(device_mod.Op1weError, msg=str(changes)):
                ctl.apply(IDENTITY, changes,
                            expected_revision=self.fresh_revision(ctl))
            self.assertEqual(fake.writes, [], f"writes happened for {changes}")

    def test_unsupported_action_writes_nothing(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply(IDENTITY, {"keys": [{"slot": 6, "action": {"kind": "special8"}}]},
                        expected_revision=self.fresh_revision(ctl))
        self.assertEqual(ctx.exception.code, "unsupported")
        self.assertEqual(fake.writes, [])

    def test_named_specials_apply(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        snap, chunks = ctl.apply(IDENTITY, {"keys": [
            {"slot": 12, "action": {"kind": "dpi-plus"}},
            {"slot": 11, "action": {"kind": "polling-switch"}},
        ]}, expected_revision=self.fresh_revision(ctl))
        self.assertGreater(chunks, 0)
        bindings = {b.slot: b for b in snap.bindings}
        self.assertEqual(bindings[12].action["kind"], "dpi-plus")
        self.assertEqual(bindings[11].action["kind"], "polling-switch")

    def test_last_click_protected(self):
        fake = FakeTransport(IDENTITY)
        # Remove every left-click except slot 1, then try to unbind slot 1.
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply(IDENTITY, {"keys": [
                {"slot": 1, "action": {"kind": "unassigned"}},
                {"slot": 12, "action": {"kind": "unassigned"}},
            ]}, expected_revision=self.fresh_revision(ctl))
        # Slot 12 has no click; slot 1 holds the only left-click.
        self.assertIn("left-click", ctx.exception.message)
        self.assertEqual(fake.writes, [])

    def test_stale_revision_writes_nothing(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply(IDENTITY, {"debounceMs": 2}, expected_revision="f" * 32)
        self.assertEqual(ctx.exception.code, "stale-revision")
        self.assertEqual(fake.writes, [])

    def test_unacked_write_reports_failure(self):
        fake = FlakyTransport(IDENTITY, drop_all_writes=True)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply_bytes(IDENTITY, {protocol.ADDR_DEBOUNCE: bytes([2, 0x53])},
                            timeout=0.3)
        self.assertEqual(ctx.exception.code, "write-failed")

    def test_readback_mismatch_reports_failure(self):
        fake = FlakyTransport(IDENTITY, corrupt_readback=True)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply_bytes(IDENTITY, {protocol.ADDR_DEBOUNCE: bytes([2, 0x53])},
                            timeout=0.3)
        self.assertEqual(ctx.exception.code, "verify-failed")

    def test_reset_plans_documented_defaults(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        mem = ctl.read_memory(identity=IDENTITY)
        writes = controller_mod.plan_reset(mem)
        # Fixture state differs from defaults (debounce 1 vs 3, keys 7-10).
        addrs = sorted(writes)
        self.assertIn(protocol.ADDR_DEBOUNCE, addrs)
        self.assertIn(0x78, addrs)  # slot 7 record
        self.assertNotIn(protocol.ADDR_RIPPLE, addrs)  # preserved (no default)
        snap, chunks, changed = ctl.restore_mem(
            IDENTITY, self._reset_target(mem, writes))
        self.assertTrue(changed)
        self.assertEqual(snap.debounce_ms, 3)

    def _reset_target(self, mem, writes):
        target = dict(mem)
        for addr, data in writes.items():
            for k, byte in enumerate(data):
                target[addr + k] = byte
        return target


class ProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")

    def test_save_list_show_delete(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        mem = ctl.read_memory(identity=IDENTITY)
        info = controller_mod.save_profile(IDENTITY, "work", mem)
        self.assertEqual(info["name"], "work")
        self.assertEqual(oct(os.stat(info["path"]).st_mode & 0o777), "0o600")
        names = [p["name"] for p in controller_mod.list_profiles()]
        self.assertEqual(names, ["work"])
        loaded, payload = controller_mod.load_profile_bytes("work")
        self.assertEqual(loaded, mem)
        controller_mod.delete_profile("work")
        self.assertEqual(controller_mod.list_profiles(), [])
        with self.assertRaises(device_mod.Op1weError):
            controller_mod.load_profile_bytes("work")

    def test_bad_names_rejected(self):
        for bad in ("", "../x", "a/b", "x" * 65):
            with self.assertRaises(device_mod.Op1weError, msg=bad):
                controller_mod.validate_profile_name(bad)


class LostAckTransport(FakeTransport):
    """Commits the first write, then loses its ACK (F-002 repro)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sends = 0
        self.lost = False

    def exchange(self, opcode, payload=b"", timeout=2.0):
        if opcode == protocol.OP_EEPROM_WRITE:
            self.sends += 1
            if not self.lost:
                self.lost = True
                super().exchange(opcode, payload, timeout)  # commit...
                raise device_mod.Op1weError("timeout", "lost ack", retryable=True)
        return super().exchange(opcode, payload, timeout)


class WrongAckTransport(FakeTransport):
    """Commits writes but echoes a wrong address in the ACK."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.sends = 0

    def exchange(self, opcode, payload=b"", timeout=2.0):
        if opcode == protocol.OP_EEPROM_WRITE:
            self.sends += 1
            addr = (payload[1] << 8) | payload[2]
            length = payload[3]
            data = bytes(payload[4:4 + length])
            for k, byte in enumerate(data):
                self.mem[addr + k] = byte
            self.writes.append((addr, data))
            from test_controller import reply
            return reply(opcode, data, addr ^ 0xFF)
        return super().exchange(opcode, payload, timeout)


class DieAfterWritesTransport(FakeTransport):
    """Dies (unplug) after committing N writes."""

    def __init__(self, *args, live_writes=1, **kwargs):
        super().__init__(*args, **kwargs)
        self.live_writes = live_writes

    def exchange(self, opcode, payload=b"", timeout=2.0):
        if opcode == protocol.OP_EEPROM_WRITE and len(self.writes) >= self.live_writes:
            self.dead = True
        return super().exchange(opcode, payload, timeout)


class FailWriteNTransport(FakeTransport):
    """Refuses (without committing) the Nth write send."""

    def __init__(self, *args, fail_on=2, **kwargs):
        super().__init__(*args, **kwargs)
        self.fail_on = fail_on
        self.send_count = 0

    def exchange(self, opcode, payload=b"", timeout=2.0):
        if opcode == protocol.OP_EEPROM_WRITE:
            self.send_count += 1
            if self.send_count == self.fail_on:
                raise device_mod.Op1weError("timeout", "dead", retryable=True)
        return super().exchange(opcode, payload, timeout)


class WriteOnceTest(unittest.TestCase):
    """F-002: uncertain writes are never replayed."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")

    def test_lost_ack_is_failure_with_one_send(self):
        fake = LostAckTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply_bytes(IDENTITY, {protocol.ADDR_DEBOUNCE: bytes([2, 0x53])},
                            timeout=2.0)
        self.assertEqual(ctx.exception.code, "write-failed")
        self.assertEqual(fake.sends, 1)
        detail = ctx.exception.detail
        self.assertTrue(os.path.exists(detail["backup"]))
        self.assertEqual(detail["address"], "00a9")
        self.assertEqual(detail["observed"], "0253")  # committed bytes observed

    def test_wrong_address_ack_is_failure(self):
        fake = WrongAckTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply_bytes(IDENTITY, {protocol.ADDR_DEBOUNCE: bytes([2, 0x53])},
                            timeout=2.0)
        self.assertEqual(ctx.exception.code, "write-failed")
        self.assertEqual(fake.sends, 1)
        self.assertIn("acknowledgement", ctx.exception.message)

    def test_unplug_after_one_chunk_stops_mutation(self):
        fake = DieAfterWritesTransport(IDENTITY, live_writes=1)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply_bytes(IDENTITY, {
                protocol.ADDR_DEBOUNCE: bytes([2, 0x53]),
                protocol.ADDR_SLEEP: bytes([12, (0x55 - 12) & 0xFF]),
            }, timeout=2.0)
        self.assertEqual(ctx.exception.code, "write-failed")
        self.assertEqual(len(fake.writes), 1)  # second chunk never sent
        self.assertEqual(ctx.exception.detail["observed"], None)


class RevisionTest(unittest.TestCase):
    """F-003: canonical revision covers payloads; apply requires it."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")

    def test_missing_revision_rejected(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply(IDENTITY, {"debounceMs": 2})
        self.assertEqual(ctx.exception.code, "invalid-input")
        self.assertEqual(fake.writes, [])

    def test_noop_apply_checks_revision(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply(IDENTITY, {}, expected_revision="0" * 32)
        self.assertEqual(ctx.exception.code, "stale-revision")
        snap, chunks = ctl.apply(IDENTITY, {},
                                 expected_revision=ctl.snapshot(IDENTITY).revision)
        self.assertEqual(chunks, 0)
        self.assertEqual(fake.writes, [])
        self.assertEqual(snap.debounce_ms, 1)

    def test_payload_edit_changes_revision(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        ctl.apply(IDENTITY, {"keys": [{"slot": 11, "action": {"kind": "key", "keys": [0x04]}}]},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        before = ctl.snapshot(IDENTITY).revision
        ctl.apply_bytes(IDENTITY, {protocol.type5_addr(11) + 2: bytes([0x05])})
        after = ctl.snapshot(IDENTITY).revision
        self.assertNotEqual(before, after)

    def test_backup_and_read_revisions_agree(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        ctl.apply(IDENTITY, {"keys": [{"slot": 11, "action": {"kind": "key", "keys": [0x04]}}]},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        read_rev = ctl.snapshot(IDENTITY).revision
        backup_rev = protocol.config_revision(ctl.read_full_backup(IDENTITY))
        self.assertEqual(read_rev, backup_rev)

    def test_stale_binding_draft_rejected(self):
        # Two readers of binding `a`: first applies `b`, second's `c`
        # with the old token fails with zero writes.
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        ctl.apply(IDENTITY, {"keys": [{"slot": 11, "action": {"kind": "key", "keys": [0x04]}}]},
                    expected_revision=ctl.snapshot(IDENTITY).revision)
        stale = ctl.snapshot(IDENTITY).revision
        ctl.apply(IDENTITY, {"keys": [{"slot": 11, "action": {"kind": "key", "keys": [0x05]}}]},
                    expected_revision=stale)
        writes_before = list(fake.writes)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply(IDENTITY, {"keys": [{"slot": 11, "action": {"kind": "key", "keys": [0x06]}}]},
                        expected_revision=stale)
        self.assertEqual(ctx.exception.code, "stale-revision")
        self.assertEqual(fake.writes, writes_before)


class RestoreGateTest(unittest.TestCase):
    """F-001: restores translate to validated changes; unknowns preserved."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")

    def test_unknown_only_target_is_noop(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        snap, chunks, changed = ctl.restore_mem(IDENTITY, {0xA0: 0x99, 0x02: 0x07})
        self.assertFalse(changed)
        self.assertEqual(chunks, 0)
        self.assertEqual(fake.writes, [])
        before = load_eeprom()
        self.assertEqual(fake.mem[0xA0], before[0xA0])

    def test_bad_checksum_record_rejected(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        target = dict(load_eeprom())
        target[0x64 + 3] ^= 0x01  # corrupt slot-2 record checksum
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.restore_mem(IDENTITY, target)
        self.assertEqual(ctx.exception.code, "invalid-input")
        self.assertEqual(fake.writes, [])

    def test_unsupported_binding_rejected(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        target = dict(load_eeprom())
        special8 = bytes([0x08, 0x00, 0x00, (0x55 - 0x08) & 0xFF])
        for k, byte in enumerate(special8):
            target[0x88 + k] = byte  # slot 11: dpi-minus -> special8
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.restore_mem(IDENTITY, target)
        self.assertEqual(ctx.exception.code, "unsupported")
        self.assertEqual(fake.writes, [])

    def test_restore_preserves_drifted_unknowns(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        backup = ctl.read_full_backup(IDENTITY)
        fake.mem[0xA0] ^= 0xFF  # firmware drift after capture
        fake.mem[protocol.ADDR_DEBOUNCE] = 9  # supported field tampered
        snap, chunks, changed = ctl.restore_mem(IDENTITY, backup)
        self.assertTrue(changed)
        self.assertEqual(fake.mem[0xA0], load_eeprom()[0xA0] ^ 0xFF)
        self.assertEqual(fake.mem[protocol.ADDR_DEBOUNCE], 1)

    def test_restore_enforces_last_click(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        target = dict(load_eeprom())
        for k, byte in enumerate(bytes([0, 0, 0, 0x55])):
            target[0x60 + k] = byte  # unbind the only left-click
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.restore_mem(IDENTITY, target)
        self.assertIn("left-click", ctx.exception.message)
        self.assertEqual(fake.writes, [])

    def test_plan_restore_sparse_and_direct(self):
        current = load_eeprom()
        changes, direct = controller_mod.plan_restore(current, {0xA0: 0x99})
        self.assertEqual((changes, direct), ({}, {}))
        target = {0xA9: 5, 0xAA: (0x55 - 5) & 0xFF}
        changes, direct = controller_mod.plan_restore(current, target)
        self.assertEqual(changes, {"debounceMs": 5})
        self.assertEqual(direct, {})
        # x != y CPI has no changes form: exact validated bytes go direct.
        target = dict(current)
        record = protocol.encode_cpi_record(400, 800)
        for k, byte in enumerate(record):
            target[protocol.ADDR_CPI + k] = byte
        changes, direct = controller_mod.plan_restore(current, target)
        self.assertIsNone(changes["cpi"][0])
        self.assertEqual(direct, {protocol.ADDR_CPI: record})


class ClickSafetyTest(unittest.TestCase):
    """F-004: usable-click quorum and install-before-remove order."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.environ["OP1WE_STATE_DIR"] = os.path.join(self.tmp.name, "state")
        os.environ["OP1WE_RUNTIME_DIR"] = os.path.join(self.tmp.name, "run")

    def test_click_beyond_ui_slots_rejected(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        revision = ctl.snapshot(IDENTITY).revision
        for slot in (13, 16):
            with self.assertRaises(device_mod.Op1weError, msg=f"slot {slot}") as ctx:
                ctl.apply(IDENTITY, {"keys": [
                    {"slot": 1, "action": {"kind": "unassigned"}},
                    {"slot": slot, "action": {"kind": "mouse", "buttons": ["left"]}},
                ]}, expected_revision=revision)
            self.assertIn("left-click", ctx.exception.message)
        self.assertEqual(fake.writes, [])

    def test_corrupt_click_does_not_count(self):
        fake = FakeTransport(IDENTITY)
        fake.mem[0x63] ^= 0xFF  # slot-1 record checksum now invalid
        ctl = controller_mod.Controller(lambda ident: fake)
        with self.assertRaises(device_mod.Op1weError) as ctx:
            ctl.apply(IDENTITY, {"keys": [
                {"slot": 1, "action": {"kind": "unassigned"}},
            ]}, expected_revision=ctl.snapshot(IDENTITY).revision)
        self.assertIn("left-click", ctx.exception.message)
        # ...but installing a fresh click elsewhere is accepted.
        snap, chunks = ctl.apply(IDENTITY, {"keys": [
            {"slot": 12, "action": {"kind": "mouse", "buttons": ["left"]}},
        ]}, expected_revision=ctl.snapshot(IDENTITY).revision)
        self.assertGreater(chunks, 0)

    def test_replacement_click_installs_first(self):
        fake = FakeTransport(IDENTITY)
        ctl = controller_mod.Controller(lambda ident: fake)
        ctl.apply(IDENTITY, {"keys": [
            {"slot": 1, "action": {"kind": "unassigned"}},
            {"slot": 4, "action": {"kind": "mouse", "buttons": ["left"]}},
        ]}, expected_revision=ctl.snapshot(IDENTITY).revision)
        self.assertEqual([addr for addr, _ in fake.writes], [0x6C, 0x60])

    def test_failed_migration_keeps_a_click(self):
        for fail_on in (1, 2):
            with self.subTest(fail_on=fail_on):
                fake = FailWriteNTransport(IDENTITY, fail_on=fail_on)
                ctl = controller_mod.Controller(lambda ident: fake)
                with self.assertRaises(device_mod.Op1weError) as ctx:
                    ctl.apply(IDENTITY, {"keys": [
                        {"slot": 1, "action": {"kind": "unassigned"}},
                        {"slot": 4, "action": {"kind": "mouse", "buttons": ["left"]}},
                    ]}, expected_revision=ctl.snapshot(IDENTITY).revision)
                self.assertEqual(ctx.exception.code, "write-failed")
                self.assertTrue(controller_mod._left_click_slots(fake.mem),
                                "no usable click left after failed migration")


class ListenTest(unittest.TestCase):
    def test_stage_frames_filtered(self):
        import socket
        import threading
        # DGRAM preserves report boundaries like hidraw reads do.
        local, peer = socket.socketpair(socket.AF_UNIX, socket.SOCK_DGRAM)
        self.addCleanup(local.close)
        self.addCleanup(peer.close)
        transport = device_mod.HidrawTransport(IDENTITY)
        transport._fd = local.fileno()
        # NOTE: keep transport un-closed (socket owned by the test).
        good = bytes([0x09, 0x0A, 0, 0, 0, 0x0A, 0x01, 0x02, 0x01]
                     + [0] * 7)
        good = good + bytes([(0x55 - sum(good)) & 0xFF])
        other = bytes([0x09, 0x04, 0, 0, 0, 0x02, 0x46, 0x00] + [0] * 8)
        other = other + bytes([(0x55 - sum(other)) & 0xFF])

        def feed():
            peer.send(other)  # valid frame, wrong opcode: skipped
            peer.send(good)  # valid stage notification: kept
            peer.send(b"short")  # runt: skipped
        timer = threading.Timer(0.1, feed)
        timer.start()
        try:
            events = device_mod.HidrawTransport.listen_stage(transport, 0.5)
        finally:
            timer.join()
            transport._fd = None
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["stage"], 2)


if __name__ == "__main__":
    unittest.main()
