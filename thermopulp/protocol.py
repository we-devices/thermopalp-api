from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
import struct


PROTOCOL_VERSION = 1
MAX_PAYLOAD_SIZE = 54
MAX_ENCODED_SIZE = 64


class MessageType(IntEnum):
    GET_INFO = 0x01
    INFO = 0x02
    START_STREAM = 0x10
    STOP_STREAM = 0x11
    SAMPLE = 0x12
    GET_SETTINGS = 0x20
    SETTINGS = 0x21
    SET_SETTING = 0x22
    ENTER_DFU = 0x30
    ACK = 0x7E
    ERROR = 0x7F


class SettingId(IntEnum):
    SAMPLE_INTERVAL_MS = 0x01
    CHANNEL_MASK = 0x02
    CHANNEL_CALIBRATION = 0x10
    RESET_CALIBRATION = 0x11


class ErrorCode(IntEnum):
    UNSUPPORTED_VERSION = 0x01
    UNSUPPORTED_MESSAGE = 0x02
    INVALID_PAYLOAD = 0x03
    VALUE_OUT_OF_RANGE = 0x04
    STORAGE_FAILURE = 0x05
    PROTECTION_LEVEL = 0x06


CAPABILITY_DFU = 1 << 3
CAPABILITY_CALIBRATION = 1 << 4
CAPABILITY_RDP_UPDATE = 1 << 5

CALIBRATION_GAIN_DEFAULT = 1_000_000
CALIBRATION_GAIN_MIN = 900_000
CALIBRATION_GAIN_MAX = 1_100_000
CALIBRATION_OFFSET_MIN = -100_000
CALIBRATION_OFFSET_MAX = 100_000
CALIBRATION_ALL_CHANNELS = 0xFF


@dataclass(frozen=True)
class Frame:
    version: int
    message_type: int
    sequence: int
    payload: bytes = b""


@dataclass(frozen=True)
class DeviceInfo:
    uptime_ms: int
    capabilities: int
    channel_count: int
    adc_count: int
    max_payload: int
    min_sample_interval_ms: int
    max_sample_interval_ms: int
    firmware_version: tuple[int, int, int] | None = None
    hardware_revision: tuple[int, int] | None = None
    rdp_level: int | None = None


@dataclass(frozen=True)
class ChannelCalibration:
    gain_ppm: int = CALIBRATION_GAIN_DEFAULT
    offset_millidegrees: int = 0

    @property
    def gain(self) -> float:
        return self.gain_ppm / 1_000_000.0

    @property
    def offset_c(self) -> float:
        return self.offset_millidegrees / 1000.0


DEFAULT_CALIBRATIONS = tuple(ChannelCalibration() for _ in range(4))


@dataclass(frozen=True)
class DeviceSettings:
    sample_interval_ms: int
    channel_mask: int
    streaming: bool
    calibrations: tuple[ChannelCalibration, ...] = DEFAULT_CALIBRATIONS


@dataclass(frozen=True)
class Sample:
    sequence: int
    uptime_ms: int
    channel_mask: int
    temperatures_c: tuple[float, float, float, float]
    statuses: tuple[int, int, int, int]
    cold_junction_c: tuple[float, float]


class ProtocolError(ValueError):
    pass


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def _cobs_encode(data: bytes) -> bytes:
    output = bytearray((0,))
    code_index = 0
    code = 1

    for value in data:
        if value == 0:
            output[code_index] = code
            code_index = len(output)
            output.append(0)
            code = 1
        else:
            output.append(value)
            code += 1
            if code == 0xFF:
                output[code_index] = code
                code_index = len(output)
                output.append(0)
                code = 1

    output[code_index] = code
    return bytes(output)


def _cobs_decode(data: bytes) -> bytes:
    if not data:
        raise ProtocolError("empty COBS frame")

    output = bytearray()
    index = 0
    while index < len(data):
        code = data[index]
        if code == 0:
            raise ProtocolError("zero byte inside COBS frame")
        index += 1
        end = index + code - 1
        if end > len(data):
            raise ProtocolError("truncated COBS frame")
        output.extend(data[index:end])
        index = end
        if code != 0xFF and index < len(data):
            output.append(0)

    return bytes(output)


def encode_frame(message_type: int, sequence: int, payload: bytes = b"") -> bytes:
    if len(payload) > MAX_PAYLOAD_SIZE:
        raise ProtocolError(f"payload exceeds {MAX_PAYLOAD_SIZE} bytes")
    if not 0 <= sequence <= 0xFFFF:
        raise ProtocolError("sequence is outside uint16 range")

    raw = struct.pack("<BBHH", PROTOCOL_VERSION, int(message_type), sequence, len(payload))
    raw += payload
    raw += struct.pack("<H", crc16_ccitt_false(raw))
    encoded = _cobs_encode(raw) + b"\x00"
    if len(encoded) > MAX_ENCODED_SIZE:
        raise ProtocolError("encoded frame exceeds one USB packet")
    return encoded


def decode_frame(encoded_without_delimiter: bytes) -> Frame:
    raw = _cobs_decode(encoded_without_delimiter)
    if len(raw) < 8:
        raise ProtocolError("frame is shorter than header plus CRC")

    version, message_type, sequence, payload_length = struct.unpack_from("<BBHH", raw)
    if payload_length > MAX_PAYLOAD_SIZE:
        raise ProtocolError("payload length exceeds protocol maximum")
    if len(raw) != 8 + payload_length:
        raise ProtocolError("payload length does not match frame length")

    received_crc = struct.unpack_from("<H", raw, len(raw) - 2)[0]
    expected_crc = crc16_ccitt_false(raw[:-2])
    if received_crc != expected_crc:
        raise ProtocolError("CRC mismatch")

    return Frame(version, message_type, sequence, raw[6:-2])


class StreamDecoder:
    def __init__(self) -> None:
        self._encoded = bytearray()
        self._discarding = False

    def reset(self) -> None:
        self._encoded.clear()
        self._discarding = False

    def feed(self, data: bytes) -> tuple[list[Frame], list[str]]:
        frames: list[Frame] = []
        errors: list[str] = []

        for value in data:
            if value != 0:
                if self._discarding:
                    continue
                if len(self._encoded) >= MAX_ENCODED_SIZE - 1:
                    self._encoded.clear()
                    self._discarding = True
                    continue
                self._encoded.append(value)
                continue

            if self._discarding:
                self._discarding = False
                errors.append("discarded overlong frame")
                continue
            if not self._encoded:
                continue

            try:
                frames.append(decode_frame(bytes(self._encoded)))
            except ProtocolError as error:
                errors.append(str(error))
            finally:
                self._encoded.clear()

        return frames, errors


def parse_info(frame: Frame) -> DeviceInfo:
    if frame.message_type != MessageType.INFO or len(frame.payload) not in (14, 19, 20):
        raise ProtocolError("invalid INFO payload")
    values = struct.unpack("<IHBBHHH", frame.payload[:14])
    if len(frame.payload) >= 19:
        return DeviceInfo(
            *values,
            firmware_version=tuple(frame.payload[14:17]),
            hardware_revision=tuple(frame.payload[17:19]),
            rdp_level=frame.payload[19] if len(frame.payload) == 20 else None,
        )
    return DeviceInfo(*values)


def parse_settings(frame: Frame) -> DeviceSettings:
    if frame.message_type != MessageType.SETTINGS or len(frame.payload) not in (6, 38):
        raise ProtocolError("invalid SETTINGS payload")
    interval, mask, streaming = struct.unpack("<IBB", frame.payload[:6])
    if len(frame.payload) == 6:
        return DeviceSettings(interval, mask, bool(streaming))
    calibrations = tuple(
        ChannelCalibration(*struct.unpack_from("<ii", frame.payload, 6 + channel * 8))
        for channel in range(4)
    )
    return DeviceSettings(interval, mask, bool(streaming), calibrations)


def parse_sample(frame: Frame) -> Sample:
    if frame.message_type != MessageType.SAMPLE or len(frame.payload) != 33:
        raise ProtocolError("invalid SAMPLE payload")
    values = struct.unpack("<IB4i4b2i", frame.payload)
    return Sample(
        sequence=frame.sequence,
        uptime_ms=values[0],
        channel_mask=values[1],
        temperatures_c=tuple(value / 1000.0 for value in values[2:6]),
        statuses=tuple(values[6:10]),
        cold_junction_c=tuple(value / 1000.0 for value in values[10:12]),
    )


def set_interval_payload(interval_ms: int) -> bytes:
    return struct.pack("<BI", SettingId.SAMPLE_INTERVAL_MS, interval_ms)


def set_channel_mask_payload(channel_mask: int) -> bytes:
    return struct.pack("<BB", SettingId.CHANNEL_MASK, channel_mask)


def set_calibration_payload(
    channel: int, gain_ppm: int, offset_millidegrees: int
) -> bytes:
    return struct.pack(
        "<BBii", SettingId.CHANNEL_CALIBRATION, channel, gain_ppm, offset_millidegrees
    )


def reset_calibration_payload(channel: int = CALIBRATION_ALL_CHANNELS) -> bytes:
    return struct.pack("<BB", SettingId.RESET_CALIBRATION, channel)
