"""Hardware-free tests for framing, codecs and record decoders."""

import json
import os
import unittest

from op1we import protocol

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


def load_handshake():
    rows = []
    with open(os.path.join(FIXTURES, "handshake.jsonl")) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                rows.append(json.loads(line))
    return rows


def load_eeprom():
    mem = {}
    with open(os.path.join(FIXTURES, "eeprom-0x00-0xb4.hex")) as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            addr_s, _, cells = line.partition(":")
            addr = int(addr_s, 16)
            for cell in cells.split():
                mem[addr] = int(cell, 16)
                addr += 1
    return mem


def load_notifications():
    frames = []
    with open(os.path.join(FIXTURES, "notify-stage.hex")) as handle:
        for line in handle:
            line = line.strip()
            if line and not line.startswith("#"):
                frames.append(bytes.fromhex(line))
    return frames


class FramingTest(unittest.TestCase):
    def test_fixture_frames_are_well_formed(self):
        for row in load_handshake():
            tx = bytes.fromhex(row["tx"])
            rx = bytes.fromhex(row["rx"])
            self.assertEqual(len(tx), 17)
            self.assertEqual(tx[0], 0x08)
            self.assertTrue(protocol.frame_is_valid(rx))
            # Rebuilt tx must match the captured bytes exactly.
            op = tx[1]
            payload = bytes(tx[2:16]).rstrip(b"\x00")
            self.assertEqual(protocol.frame(op, payload), tx)

    def test_model_query_matches_hardware_capture(self):
        self.assertEqual(protocol.frame(protocol.OP_MODEL, protocol.MODEL_QUERY).hex(),
                         "0801000000080000000000000000000044")
        captured = bytes.fromhex("09010000000835020000350200000000d5")
        self.assertEqual(protocol.parse_model(captured), (0x35, 0x02))
        for index in (0, 1, 2, 5, 16):
            malformed = bytearray(captured)
            malformed[index] ^= 1
            if index != 16:
                malformed[16] = (0x55 - sum(malformed[:16])) & 0xff
            with self.assertRaises(ValueError):
                protocol.parse_model(bytes(malformed))

    def test_checksum_rule(self):
        self.assertEqual(protocol.checksum(bytes(16)), 0x55)
        self.assertEqual(protocol.checksum(bytes([0x08, 0x04] + [0] * 14)), 0x49)

    def test_allowlist_rejects_unknown_opcodes(self):
        for op in (0x00, 0x02, 0x05, 0x06, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x10):
            with self.assertRaises(ValueError, msg=f"op 0x{op:02x}"):
                protocol.frame(op)
        for op in (0x01, 0x03, 0x04, 0x07, 0x08, 0x0F):
            protocol.frame(op)  # must not raise

    def test_oversize_payload_rejected(self):
        with self.assertRaises(ValueError):
            protocol.frame(0x08, bytes(16))
        with self.assertRaises(ValueError):
            protocol.ee_read_payload(0x00, 11)
        with self.assertRaises(ValueError):
            protocol.ee_write_payload(0x00, bytes(11))

    def test_write_outside_config_region_rejected(self):
        with self.assertRaises(ValueError):
            protocol.ee_write_payload(0xB5, b"\x00")
        with self.assertRaises(ValueError):
            protocol.ee_write_payload(0xB4, b"\x00\x00")  # straddles the end

    def test_reply_validation(self):
        good = bytes.fromhex("0904000000024600000000000000000000")
        self.assertEqual(protocol.parse_battery(good), (70, 0))
        bad_checksum = bytearray(good)
        bad_checksum[16] ^= 0x01
        with self.assertRaises(ValueError):
            protocol.parse_battery(bytes(bad_checksum))
        with self.assertRaises(ValueError):
            protocol.parse_battery(good[:10])
        wrong_op = bytearray(good)
        wrong_op[1] = 0x03
        wrong_op[16] = protocol.checksum(bytes(wrong_op[:16]))
        with self.assertRaises(ValueError):
            protocol.parse_battery(bytes(wrong_op))

    def test_battery_bounds(self):
        base = bytearray.fromhex("0904000000024600000000000000000000")
        base[6] = 101
        base[16] = protocol.checksum(bytes(base[:16]))
        with self.assertRaises(ValueError):
            protocol.parse_battery(bytes(base))
        base[6] = 100
        base[16] = protocol.checksum(bytes(base[:16]))
        self.assertEqual(protocol.parse_battery(bytes(base)), (100, 0))

    def test_stage_notifications(self):
        stages = [protocol.parse_stage_notification(f) for f in load_notifications()]
        self.assertEqual(stages, [3, 0, 1, 2])
        with self.assertRaises(ValueError):
            protocol.parse_stage_notification(bytes.fromhex("0904000000024600000000000000000000"))


class EepromDecodeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mem = load_eeprom()

    def test_fixture_size(self):
        self.assertEqual(len(self.mem), 0xB5)
        self.assertEqual(min(self.mem), 0x00)
        self.assertEqual(max(self.mem), 0xB4)

    def test_polling(self):
        self.assertEqual(protocol.decode_polling_hz(self.mem), 1000)

    def test_debounce(self):
        self.assertEqual(protocol.decode_pair(self.mem, 0xA9), 1)

    def test_cpi_slots_match_factory_defaults(self):
        got = []
        for slot in range(8):
            base = 0x0C + slot * 4
            raw = bytes(self.mem[base + k] for k in range(4))
            got.append(protocol.decode_cpi_record(raw))
        self.assertEqual(
            got,
            [(400, 400), (800, 800), (1600, 1600), (3200, 3200),
             (3200, 3200), (3200, 3200), (3200, 3200), (3200, 3200)],
        )

    def test_cpi_multiplier_refused(self):
        with self.assertRaises(ValueError):
            protocol.decode_cpi_record(bytes([0x07, 0x07, 0x01, 0x46]))

    def test_cpi_legal_values(self):
        self.assertTrue(protocol.cpi_value_is_legal(50))
        self.assertTrue(protocol.cpi_value_is_legal(10000))
        self.assertFalse(protocol.cpi_value_is_legal(10050))  # the knee gap
        self.assertTrue(protocol.cpi_value_is_legal(10100))
        self.assertTrue(protocol.cpi_value_is_legal(19000))
        self.assertFalse(protocol.cpi_value_is_legal(19050))
        self.assertFalse(protocol.cpi_value_is_legal(49))

    def test_key_records(self):
        mem = self.mem
        first = bytes(mem[0x60 + k] for k in range(4))
        decoded = protocol.decode_key_record(first)
        self.assertEqual(decoded["kind"], "mouse")
        self.assertEqual(decoded["buttons"], ["left"])
        self.assertTrue(decoded["checksum_ok"])
        fifth = bytes(mem[0x70 + k] for k in range(4))
        self.assertEqual(protocol.decode_key_record(fifth)["buttons"], ["forward"])
        # Behavior-confirmed special types decode to named actions.
        sixth = bytes(mem[0x74 + k] for k in range(4))
        decoded6 = protocol.decode_key_record(sixth)
        self.assertEqual(decoded6["kind"], "dpi-toggle")
        self.assertTrue(decoded6["checksum_ok"])
        self.assertEqual(protocol.encode_key_record("dpi-toggle"), sixth)
        self.assertEqual(protocol.encode_key_record("dpi-plus"),
                         bytes([0x02, 0x02, 0x00, 0x51]))
        self.assertEqual(protocol.encode_key_record("dpi-minus"),
                         bytes([0x02, 0x03, 0x00, 0x50]))
        self.assertEqual(protocol.encode_key_record("polling-switch"),
                         bytes([0x07, 0x00, 0x00, 0x4E]))
        # Still-unconfirmed specials keep neutral kinds, never mapped.
        eighth = bytes(mem[0x7C + k] for k in range(4))
        self.assertEqual(protocol.decode_key_record(eighth)["kind"], "special8")
        with self.assertRaises(ValueError):
            protocol.encode_key_record("special8")
        empty = bytes(mem[0x8C + k] for k in range(4))
        self.assertEqual(protocol.decode_key_record(empty)["kind"], "unassigned")

    def test_revision_stable(self):
        rev = protocol.config_revision(self.mem)
        self.assertEqual(len(rev), 32)
        altered = dict(self.mem)
        altered[0xA9] ^= 0x01
        self.assertNotEqual(protocol.config_revision(altered), rev)

    def test_revision_is_addressed(self):
        self.assertNotEqual(protocol.config_revision({0: 1, 1: 2}),
                            protocol.config_revision({5: 1, 6: 2}))

    def test_battery_missing_charging_is_unknown(self):
        base = bytearray.fromhex("0904000000024600000000000000000000")
        base[5] = 1
        base[16] = protocol.checksum(bytes(base[:16]))
        self.assertEqual(protocol.parse_battery(bytes(base)), (70, None))


if __name__ == "__main__":
    unittest.main()
