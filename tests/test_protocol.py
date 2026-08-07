import struct
import unittest

from thermopulp.protocol import (
    Frame,
    MAX_PAYLOAD_SIZE,
    MessageType,
    ProtocolError,
    StreamDecoder,
    crc16_ccitt_false,
    decode_frame,
    encode_frame,
    parse_info,
    parse_settings,
    parse_sample,
    reset_calibration_payload,
    set_calibration_payload,
)


class ProtocolTests(unittest.TestCase):
    def test_firmware_golden_vector(self) -> None:
        encoded = encode_frame(MessageType.GET_INFO, 0x1234)
        self.assertEqual(encoded.hex(" ").upper(), "05 01 01 34 12 01 03 FA 2A 00")
        self.assertEqual(
            decode_frame(encoded[:-1]),
            Frame(1, MessageType.GET_INFO, 0x1234, b""),
        )

    def test_crc_standard_vector(self) -> None:
        self.assertEqual(crc16_ccitt_false(b"123456789"), 0x29B1)

    def test_fragmented_stream(self) -> None:
        first = encode_frame(MessageType.GET_INFO, 1)
        second = encode_frame(MessageType.GET_SETTINGS, 2)
        decoder = StreamDecoder()
        frames_a, errors_a = decoder.feed((first + second)[:7])
        frames_b, errors_b = decoder.feed((first + second)[7:])
        self.assertEqual(frames_a, [])
        self.assertEqual(errors_a + errors_b, [])
        self.assertEqual([frame.sequence for frame in frames_b], [1, 2])

    def test_maximum_frame_size(self) -> None:
        payload = bytes(range(1, MAX_PAYLOAD_SIZE + 1))
        self.assertLessEqual(len(encode_frame(MessageType.SET_SETTING, 3, payload)), 64)

    def test_crc_error(self) -> None:
        encoded = bytearray(encode_frame(MessageType.GET_INFO, 1))
        encoded[-2] ^= 1
        with self.assertRaises(ProtocolError):
            decode_frame(bytes(encoded[:-1]))

    def test_sample_payload(self) -> None:
        payload = struct.pack(
            "<IB4i4b2i",
            1500,
            0x0F,
            20125,
            -500,
            30000,
            41250,
            0,
            0,
            -2,
            0,
            25000,
            26000,
        )
        sample = parse_sample(Frame(1, MessageType.SAMPLE, 8, payload))
        self.assertEqual(sample.uptime_ms, 1500)
        self.assertEqual(sample.temperatures_c, (20.125, -0.5, 30.0, 41.25))
        self.assertEqual(sample.statuses, (0, 0, -2, 0))

    def test_extended_device_info(self) -> None:
        payload = struct.pack("<IHBBHHH", 1234, 7, 4, 2, 54, 250, 60000)
        payload += bytes((0, 1, 0, 1, 0))
        info = parse_info(Frame(1, MessageType.INFO, 4, payload))
        self.assertEqual(info.firmware_version, (0, 1, 0))
        self.assertEqual(info.hardware_revision, (1, 0))

    def test_device_info_includes_rdp_level(self) -> None:
        payload = struct.pack("<IHBBHHH", 1234, 0x3F, 4, 2, 54, 250, 60000)
        payload += bytes((0, 4, 0, 1, 0, 1))

        info = parse_info(Frame(1, MessageType.INFO, 4, payload))

        self.assertEqual(info.firmware_version, (0, 4, 0))
        self.assertEqual(info.rdp_level, 1)

    def test_legacy_device_info(self) -> None:
        payload = struct.pack("<IHBBHHH", 1234, 7, 4, 2, 54, 250, 60000)
        info = parse_info(Frame(1, MessageType.INFO, 4, payload))
        self.assertIsNone(info.firmware_version)
        self.assertIsNone(info.hardware_revision)

    def test_extended_settings_include_channel_calibration(self) -> None:
        payload = struct.pack("<IBB", 1000, 0x0F, 1)
        payload += b"".join(
            struct.pack("<ii", gain, offset)
            for gain, offset in (
                (1_000_000, 0),
                (1_001_000, -250),
                (999_000, 500),
                (1_010_000, -1000),
            )
        )

        settings = parse_settings(Frame(1, MessageType.SETTINGS, 7, payload))

        self.assertEqual(settings.calibrations[1].gain_ppm, 1_001_000)
        self.assertEqual(settings.calibrations[1].offset_c, -0.25)
        self.assertEqual(settings.calibrations[3].gain, 1.01)

    def test_legacy_settings_use_identity_calibration(self) -> None:
        payload = struct.pack("<IBB", 1000, 0x0F, 0)
        settings = parse_settings(Frame(1, MessageType.SETTINGS, 7, payload))
        self.assertTrue(all(item.gain_ppm == 1_000_000 for item in settings.calibrations))
        self.assertTrue(all(item.offset_millidegrees == 0 for item in settings.calibrations))

    def test_calibration_setting_payloads(self) -> None:
        self.assertEqual(
            set_calibration_payload(2, 1_002_500, -750),
            struct.pack("<BBii", 0x10, 2, 1_002_500, -750),
        )
        self.assertEqual(reset_calibration_payload(0xFF), bytes((0x11, 0xFF)))


if __name__ == "__main__":
    unittest.main()
