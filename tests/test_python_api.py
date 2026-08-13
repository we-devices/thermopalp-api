from __future__ import annotations

from types import SimpleNamespace
import struct
import unittest
from unittest.mock import patch

from thermopalp import (
    DeviceRejectedError,
    ThermopalpDevice,
    discover_devices,
)
from thermopalp.protocol import (
    ErrorCode,
    MessageType,
    SettingId,
    decode_frame,
    encode_frame,
)


class FakeSerial:
    def __init__(self) -> None:
        self.port = "COM42"
        self.is_open = True
        self.input = bytearray()
        self.commands: list[MessageType] = []
        self.interval_ms = 1000
        self.channel_mask = 0x0F
        self.streaming = False
        self.reject_settings = False

    @property
    def in_waiting(self) -> int:
        return len(self.input)

    def reset_input_buffer(self) -> None:
        self.input.clear()

    def reset_output_buffer(self) -> None:
        pass

    def write(self, encoded: bytes) -> int:
        frame = decode_frame(encoded[:-1])
        message_type = MessageType(frame.message_type)
        self.commands.append(message_type)

        if message_type == MessageType.GET_INFO:
            payload = struct.pack(
                "<IHBBHHH3B2BB",
                12_345,
                0x003F,
                4,
                2,
                54,
                250,
                60_000,
                0,
                6,
                0,
                1,
                0,
                1,
            )
            self._queue(MessageType.INFO, frame.sequence, payload)
        elif message_type == MessageType.GET_SETTINGS:
            payload = struct.pack(
                "<IBB8i",
                self.interval_ms,
                self.channel_mask,
                int(self.streaming),
                1_000_000,
                0,
                1_000_000,
                0,
                1_000_000,
                0,
                1_000_000,
                0,
            )
            self._queue(MessageType.SETTINGS, frame.sequence, payload)
        elif message_type == MessageType.SET_SETTING and self.reject_settings:
            self._queue(
                MessageType.ERROR,
                frame.sequence,
                bytes((MessageType.SET_SETTING, ErrorCode.VALUE_OUT_OF_RANGE)),
            )
        elif message_type == MessageType.SET_SETTING:
            if frame.payload[0] == SettingId.SAMPLE_INTERVAL_MS:
                self.interval_ms = struct.unpack_from("<I", frame.payload, 1)[0]
            elif frame.payload[0] == SettingId.CHANNEL_MASK:
                self.channel_mask = frame.payload[1]
            self._ack(frame.sequence, message_type)
        elif message_type == MessageType.START_STREAM:
            self.streaming = True
            self._ack(frame.sequence, message_type)
            self._sample(1000, 1)
            self._sample(2000, 2)
        elif message_type == MessageType.STOP_STREAM:
            self.streaming = False
            self._ack(frame.sequence, message_type)
        return len(encoded)

    def flush(self) -> None:
        pass

    def read(self, size: int) -> bytes:
        data = bytes(self.input[:size])
        del self.input[:size]
        return data

    def close(self) -> None:
        self.is_open = False

    def _ack(self, sequence: int, request: MessageType) -> None:
        self._queue(MessageType.ACK, sequence, bytes((request,)))

    def _sample(self, uptime_ms: int, sequence: int) -> None:
        payload = struct.pack(
            "<IB4i4b2i",
            uptime_ms,
            0x0F,
            25_100,
            25_200,
            25_300,
            25_400,
            0,
            0,
            0,
            0,
            24_900,
            25_000,
        )
        self._queue(MessageType.SAMPLE, sequence, payload)

    def _queue(self, message_type: MessageType, sequence: int, payload: bytes) -> None:
        self.input.extend(encode_frame(message_type, sequence, payload))


class PythonApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.transport = FakeSerial()
        patcher = patch(
            "thermopalp.device.serial.Serial", return_value=self.transport
        )
        self.addCleanup(patcher.stop)
        patcher.start()

    def test_context_manager_reads_metadata_and_samples(self) -> None:
        with ThermopalpDevice("COM42") as device:
            self.assertTrue(device.connected)
            self.assertEqual(device.device_info.firmware_version, (0, 6, 0))
            self.assertEqual(device.settings.sample_interval_ms, 1000)

            samples = device.read_samples(2)

            self.assertEqual([sample.uptime_ms for sample in samples], [1000, 2000])
            self.assertEqual(samples[0].temperatures_c[0], 25.1)
            self.assertFalse(device.streaming)

        self.assertFalse(self.transport.is_open)
        self.assertIn(MessageType.START_STREAM, self.transport.commands)
        self.assertGreaterEqual(
            self.transport.commands.count(MessageType.STOP_STREAM), 2
        )

    def test_settings_are_applied_and_read_back(self) -> None:
        with ThermopalpDevice("COM42") as device:
            settings = device.set_sample_interval(2500)
            settings = device.set_channel_mask(0x05)

        self.assertEqual(settings.sample_interval_ms, 2500)
        self.assertEqual(settings.channel_mask, 0x05)

    def test_stop_discards_samples_queued_before_stop_ack(self) -> None:
        with ThermopalpDevice("COM42") as device:
            device.start_streaming()
            self.assertTrue(
                any(frame.message_type == MessageType.SAMPLE for frame in device._inbox)
            )

            device.stop_streaming()

            self.assertFalse(
                any(frame.message_type == MessageType.SAMPLE for frame in device._inbox)
            )

    def test_device_error_becomes_typed_exception(self) -> None:
        with ThermopalpDevice("COM42") as device:
            self.transport.reject_settings = True
            with self.assertRaises(DeviceRejectedError) as context:
                device.set_sample_interval(2000)

        self.assertEqual(context.exception.request_type, MessageType.SET_SETTING)
        self.assertEqual(context.exception.error_code, ErrorCode.VALUE_OUT_OF_RANGE)

    def test_discovery_filters_unrelated_serial_ports(self) -> None:
        ports = [
            SimpleNamespace(
                device="COM1",
                name="COM1",
                description="Unrelated adapter",
                manufacturer="Example",
                serial_number="A",
                vid=0x1234,
                pid=0x5678,
            ),
            SimpleNamespace(
                device="COM42",
                name="COM42",
                description="Thermopalp",
                manufacturer="Thermopalp",
                serial_number="TP001",
                vid=0x0483,
                pid=0x5740,
            ),
        ]
        with patch("thermopalp.device.list_ports.comports", return_value=ports):
            devices = discover_devices()

        self.assertEqual([device.port for device in devices], ["COM42"])
        self.assertEqual(devices[0].serial_number, "TP001")


if __name__ == "__main__":
    unittest.main()
