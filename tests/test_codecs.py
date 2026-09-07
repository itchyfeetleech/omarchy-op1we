"""Milestone-2 codec tests: encoders, type-5 payloads, advanced fields."""

import unittest

from op1we import protocol
from test_protocol import load_eeprom


class EncoderTest(unittest.TestCase):
    def test_polling_round_trip(self):
        for hertz, mask in ((125, 0x08), (250, 0x04), (500, 0x02), (1000, 0x01)):
            encoded = protocol.encode_polling_mask(hertz)
            self.assertEqual(encoded[0], mask)
            self.assertEqual(encoded[1], (0x55 - mask) & 0xFF)
        with self.assertRaises(ValueError):
            protocol.encode_polling_mask(2000)

    def test_cpi_encode_decode_round_trip(self):
        for cpi in (50, 100, 400, 800, 1600, 3200, 9950, 10000):
            record = protocol.encode_cpi_record(cpi, cpi)
            self.assertEqual(protocol.decode_cpi_record(record), (cpi, cpi))

    def test_cpi_rejects_above_knee_and_off_step(self):
        for cpi in (10100, 12000, 19000, 10050, 49, 75, 0):
            with self.assertRaises(ValueError, msg=f"cpi={cpi}"):
                protocol.encode_cpi_value(cpi)

    def test_debounce_range(self):
        self.assertEqual(protocol.encode_debounce_ms(0), bytes([0, 0x55]))
        self.assertEqual(protocol.encode_debounce_ms(30), bytes([30, (0x55 - 30) & 0xFF]))
        for bad in (-1, 31, 255):
            with self.assertRaises(ValueError):
                protocol.encode_debounce_ms(bad)

    def test_sleep_scaling(self):
        self.assertEqual(protocol.encode_sleep_s(60), bytes([6, (0x55 - 6) & 0xFF]))
        self.assertEqual(protocol.encode_sleep_s(0), bytes([0, 0x55]))
        for bad in (-10, 5, 61, 2560):
            with self.assertRaises(ValueError, msg=f"sleep={bad}"):
                protocol.encode_sleep_s(bad)

    def test_fixture_advanced_fields(self):
        mem = load_eeprom()
        self.assertEqual(protocol.decode_sleep_s(mem), 60)
        self.assertEqual(protocol.decode_pair(mem, protocol.ADDR_RIPPLE), 0)
        self.assertEqual(protocol.decode_pair(mem, protocol.ADDR_FIXLINE), 0)
        self.assertEqual(protocol.decode_pair(mem, protocol.ADDR_TURN_OFF_LIGHT), 1)
        self.assertEqual(protocol.decode_pair(mem, protocol.ADDR_STAGE_COUNT), 4)
        self.assertEqual(protocol.decode_pair(mem, protocol.ADDR_CURRENT_STAGE), 2)

    def test_type5_addr_bounds(self):
        self.assertEqual(protocol.type5_addr(1), 0x100)
        self.assertEqual(protocol.type5_addr(12), 0x100 + 11 * 0x20)
        for bad in (0, 13, 16):
            with self.assertRaises(ValueError):
                protocol.type5_addr(bad)

    def test_write_region_gate(self):
        protocol.ee_write_payload(0x00, b"\x01\x54")
        protocol.ee_write_payload(0x100, bytes(10))
        protocol.ee_write_payload(0x278, bytes(8))
        for addr, length in ((0xB5, 1), (0x280, 1), (0x300, 4), (0x100, 11)):
            with self.assertRaises(ValueError, msg=f"{addr:#x}+{length}"):
                protocol.ee_write_payload(addr, bytes(length))


class Type5Test(unittest.TestCase):
    def test_media_payload_shape(self):
        payload = protocol.build_media_payload(0xCD)
        self.assertEqual(
            payload, bytes([0x02, 0x82, 0xCD, 0x00, 0x42, 0xCD, 0x00])
            + bytes([(0x55 - 0x02 - 0x82 - 0xCD - 0x00 - 0x42 - 0xCD - 0x00) & 0xFF]),
        )
        with self.assertRaises(ValueError):
            protocol.build_media_payload(0x1234)

    def test_key_payload_shape(self):
        payload = protocol.build_key_payload([0x04])
        self.assertEqual(
            payload[:7], bytes([0x02, 0x81, 0x04, 0x00, 0x41, 0x04, 0x00])
        )
        self.assertEqual(payload[7], (0x55 - sum(payload[:7])) & 0xFF)

    def test_combo_payload_order(self):
        payload = protocol.build_key_payload([0x04, 0x05], 0x01)
        events = payload[1:-1]
        self.assertEqual(
            list(events),
            [0x80, 0x01, 0x00, 0x81, 0x04, 0x00, 0x81, 0x05, 0x00,
             0x40, 0x01, 0x00, 0x41, 0x05, 0x00, 0x41, 0x04, 0x00],
        )
        self.assertEqual(payload[0], 6)

    def test_combo_limits(self):
        with self.assertRaises(ValueError):
            protocol.build_key_payload([])
        with self.assertRaises(ValueError):
            protocol.build_key_payload([0x04] * 4)
        with self.assertRaises(ValueError):
            protocol.build_key_payload([0x04], 0x10)
        with self.assertRaises(ValueError):
            protocol.build_key_payload([0x03])
        # 4 modifiers + 3 keys = 44 bytes: exceeds the 32-byte slot.
        with self.assertRaises(ValueError):
            protocol.build_key_payload([0x04, 0x05, 0x06], 0x0F)

    def test_parse_round_trip(self):
        for payload in (protocol.build_media_payload(0xE9),
                        protocol.build_key_payload([0x1E], 0x02)):
            slot = payload + bytes([0xFF] * (32 - len(payload)))
            parsed = protocol.parse_type5_payload(slot)
            self.assertIn(parsed["kind"], ("media", "combo"))
        empty = protocol.parse_type5_payload(bytes([0xFF] * 32))
        self.assertEqual(empty["kind"], "empty")

    def test_parse_rejects_garbage(self):
        bad = bytes([0x02, 0x99, 0x00, 0x00, 0x99, 0x00, 0x00, 0x00])
        parsed = protocol.parse_type5_payload(bad + bytes([0xFF] * 24))
        self.assertEqual(parsed["kind"], "unknown")


class KeyRecordTest(unittest.TestCase):
    def test_mouse_encode(self):
        record = protocol.encode_key_record("mouse", buttons=["left", "back"])
        self.assertEqual(record[:3], bytes([0x01, 0x09, 0x00]))
        decoded = protocol.decode_key_record(record)
        self.assertEqual(decoded["kind"], "mouse")
        self.assertEqual(set(decoded["buttons"]), {"left", "back"})
        with self.assertRaises(ValueError):
            protocol.encode_key_record("mouse", buttons=["nope"])
        with self.assertRaises(ValueError):
            protocol.encode_key_record("mouse", buttons=[])

    def test_unassigned_encode(self):
        self.assertEqual(protocol.encode_key_record("unassigned"),
                         bytes([0, 0, 0, 0x55]))

    def test_specials_never_constructed(self):
        for kind in ("special4", "special8", "special9", "macro",
                     "unknown", "nope"):
            with self.assertRaises(ValueError, msg=kind):
                protocol.encode_key_record(kind)


if __name__ == "__main__":
    unittest.main()
