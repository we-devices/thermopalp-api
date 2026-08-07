from __future__ import annotations

from collections import deque
from collections.abc import Iterator
from dataclasses import dataclass, replace
import math
import threading
import time

import serial
from serial.tools import list_ports

from .protocol import (
    CALIBRATION_ALL_CHANNELS,
    CALIBRATION_GAIN_MAX,
    CALIBRATION_GAIN_MIN,
    CALIBRATION_OFFSET_MAX,
    CALIBRATION_OFFSET_MIN,
    ChannelCalibration,
    DeviceInfo,
    DeviceSettings,
    ErrorCode,
    Frame,
    MessageType,
    PROTOCOL_VERSION,
    ProtocolError,
    Sample,
    StreamDecoder,
    encode_frame,
    parse_info,
    parse_sample,
    parse_settings,
    reset_calibration_payload,
    set_calibration_payload,
    set_channel_mask_payload,
    set_interval_payload,
)


THERMOPULP_USB_VID = 0x0483
THERMOPULP_USB_PID = 0x5740


class ThermopulpError(Exception):
    """Base class for Thermopulp API failures."""


class ThermopulpConnectionError(ThermopulpError):
    """The serial device could not be found, opened, or accessed."""


class ThermopulpTimeoutError(ThermopulpError):
    """The device did not send the expected response in time."""


class ThermopulpProtocolError(ThermopulpError):
    """The device sent malformed or unsupported protocol data."""


class DeviceRejectedError(ThermopulpError):
    """The device rejected a valid host command."""

    def __init__(self, request_type: int, error_code: int) -> None:
        self.request_type = request_type
        self.error_code = error_code
        try:
            request_name = MessageType(request_type).name
        except ValueError:
            request_name = f"0x{request_type:02X}"
        try:
            error_name = ErrorCode(error_code).name.replace("_", " ").title()
        except ValueError:
            error_name = f"Unknown error {error_code}"
        super().__init__(f"Device rejected {request_name}: {error_name}")


@dataclass(frozen=True)
class DeviceDescriptor:
    port: str
    name: str
    description: str
    manufacturer: str
    serial_number: str
    vendor_id: int | None
    product_id: int | None

    @property
    def is_thermopulp(self) -> bool:
        identity_match = (
            self.vendor_id == THERMOPULP_USB_VID
            and self.product_id == THERMOPULP_USB_PID
        )
        text = " ".join(
            (self.name, self.description, self.manufacturer)
        ).lower()
        return identity_match or "thermopulp" in text

    @property
    def display_name(self) -> str:
        label = self.description or self.manufacturer or self.name or "Thermopulp"
        serial_suffix = f" · {self.serial_number}" if self.serial_number else ""
        return f"{label} ({self.port}){serial_suffix}"


def discover_devices(*, include_all_serial: bool = False) -> list[DeviceDescriptor]:
    """Return connected Thermopulp devices, or every serial port when requested."""

    devices = [
        DeviceDescriptor(
            port=info.device,
            name=info.name or "",
            description=info.description or "",
            manufacturer=info.manufacturer or "",
            serial_number=info.serial_number or "",
            vendor_id=info.vid,
            product_id=info.pid,
        )
        for info in list_ports.comports()
    ]
    if not include_all_serial:
        devices = [device for device in devices if device.is_thermopulp]
    return sorted(devices, key=lambda device: (not device.is_thermopulp, device.port))


class ThermopulpDevice:
    """Synchronous, UI-independent connection to one Thermopulp device.

    The class is safe for sequential use from one or more threads. Only one
    consumer should read samples from a connection at a time.
    """

    def __init__(
        self,
        port: str | DeviceDescriptor | None = None,
        *,
        timeout: float = 2.0,
    ) -> None:
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        self._requested_port = port
        self.timeout = float(timeout)
        self._serial: serial.Serial | None = None
        self._decoder = StreamDecoder()
        self._inbox: deque[Frame] = deque()
        self._sequence = 0
        self._lock = threading.RLock()
        self._streaming = False
        self.device_info: DeviceInfo | None = None
        self.settings: DeviceSettings | None = None

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    @property
    def port(self) -> str | None:
        if self._serial is not None:
            return str(self._serial.port)
        if isinstance(self._requested_port, DeviceDescriptor):
            return self._requested_port.port
        return self._requested_port

    @property
    def streaming(self) -> bool:
        return self._streaming

    def connect(self) -> ThermopulpDevice:
        """Open the device, stop stale streaming, and read its metadata."""

        with self._lock:
            if self.connected:
                return self
            port = self._resolve_port()
            try:
                self._serial = serial.Serial(
                    port=port,
                    baudrate=115200,
                    bytesize=serial.EIGHTBITS,
                    parity=serial.PARITY_NONE,
                    stopbits=serial.STOPBITS_ONE,
                    timeout=min(0.05, self.timeout),
                    write_timeout=self.timeout,
                )
                self._serial.reset_input_buffer()
                self._serial.reset_output_buffer()
                self._decoder.reset()
                self._inbox.clear()
                self._streaming = False
                self.stop_streaming()
                self.device_info = self.get_info()
                self.settings = self.get_settings()
            except ThermopulpError:
                self._close_transport()
                raise
            except (OSError, serial.SerialException) as error:
                self._close_transport()
                raise ThermopulpConnectionError(
                    f"Could not open {port}: {error}. Disconnect the Thermopulp UI "
                    "or any other serial client using this port."
                ) from error
            return self

    def close(self) -> None:
        """Stop streaming when possible and close the serial port."""

        with self._lock:
            if not self.connected:
                self._close_transport()
                return
            try:
                if self._streaming:
                    self.stop_streaming()
            except ThermopulpError:
                pass
            self._close_transport()

    def __enter__(self) -> ThermopulpDevice:
        return self.connect()

    def __exit__(self, *_exc_info: object) -> None:
        self.close()

    def get_info(self, *, timeout: float | None = None) -> DeviceInfo:
        with self._lock:
            frame = self._request(
                MessageType.GET_INFO, MessageType.INFO, timeout=timeout
            )
            try:
                info = parse_info(frame)
            except ProtocolError as error:
                raise ThermopulpProtocolError(str(error)) from error
            self.device_info = info
            return info

    def get_settings(self, *, timeout: float | None = None) -> DeviceSettings:
        with self._lock:
            frame = self._request(
                MessageType.GET_SETTINGS, MessageType.SETTINGS, timeout=timeout
            )
            try:
                settings = parse_settings(frame)
            except ProtocolError as error:
                raise ThermopulpProtocolError(str(error)) from error
            self.settings = settings
            self._streaming = settings.streaming
            return settings

    def start_streaming(self, *, timeout: float | None = None) -> None:
        with self._lock:
            if self._streaming:
                return
            self._command(MessageType.START_STREAM, timeout=timeout)
            self._streaming = True
            if self.settings is not None:
                self.settings = replace(self.settings, streaming=True)

    def stop_streaming(self, *, timeout: float | None = None) -> None:
        with self._lock:
            self._command(MessageType.STOP_STREAM, timeout=timeout)
            self._streaming = False
            self._discard_queued_samples()
            if self.settings is not None:
                self.settings = replace(self.settings, streaming=False)

    def set_sample_interval(
        self, interval_ms: int, *, timeout: float | None = None
    ) -> DeviceSettings:
        if not isinstance(interval_ms, int):
            raise TypeError("interval_ms must be an integer")
        minimum = self.device_info.min_sample_interval_ms if self.device_info else 250
        maximum = self.device_info.max_sample_interval_ms if self.device_info else 60_000
        if not minimum <= interval_ms <= maximum:
            raise ValueError(
                f"interval_ms must be between {minimum} and {maximum}"
            )
        with self._lock:
            self._command(
                MessageType.SET_SETTING,
                set_interval_payload(interval_ms),
                timeout=timeout,
            )
            return self.get_settings(timeout=timeout)

    def set_channel_mask(
        self, channel_mask: int, *, timeout: float | None = None
    ) -> DeviceSettings:
        if not isinstance(channel_mask, int):
            raise TypeError("channel_mask must be an integer")
        if channel_mask < 1 or channel_mask > 0x0F:
            raise ValueError("channel_mask must enable at least one of channels 1-4")
        with self._lock:
            self._command(
                MessageType.SET_SETTING,
                set_channel_mask_payload(channel_mask),
                timeout=timeout,
            )
            return self.get_settings(timeout=timeout)

    def set_calibration(
        self,
        channel: int,
        gain_ppm: int,
        offset_millidegrees: int,
        *,
        timeout: float | None = None,
    ) -> DeviceSettings:
        if not 0 <= channel < 4:
            raise ValueError("channel must be in the zero-based range 0-3")
        if not CALIBRATION_GAIN_MIN <= gain_ppm <= CALIBRATION_GAIN_MAX:
            raise ValueError(
                f"gain_ppm must be between {CALIBRATION_GAIN_MIN} and "
                f"{CALIBRATION_GAIN_MAX}"
            )
        if not CALIBRATION_OFFSET_MIN <= offset_millidegrees <= CALIBRATION_OFFSET_MAX:
            raise ValueError(
                "offset_millidegrees must be between "
                f"{CALIBRATION_OFFSET_MIN} and {CALIBRATION_OFFSET_MAX}"
            )
        with self._lock:
            self._command(
                MessageType.SET_SETTING,
                set_calibration_payload(channel, gain_ppm, offset_millidegrees),
                timeout=timeout,
            )
            return self.get_settings(timeout=timeout)

    def reset_calibration(
        self, channel: int | None = None, *, timeout: float | None = None
    ) -> DeviceSettings:
        target = CALIBRATION_ALL_CHANNELS if channel is None else channel
        if target != CALIBRATION_ALL_CHANNELS and not 0 <= target < 4:
            raise ValueError("channel must be in the zero-based range 0-3, or None")
        with self._lock:
            self._command(
                MessageType.SET_SETTING,
                reset_calibration_payload(target),
                timeout=timeout,
            )
            return self.get_settings(timeout=timeout)

    def read_sample(self, *, timeout: float | None = None) -> Sample:
        """Read one sample after :meth:`start_streaming` has succeeded."""

        with self._lock:
            self._require_connected()
            if not self._streaming:
                raise ThermopulpError("Streaming is not active")
            deadline = time.monotonic() + self._effective_timeout(timeout)
            while True:
                for index, frame in enumerate(self._inbox):
                    if frame.message_type == MessageType.SAMPLE:
                        del self._inbox[index]
                        try:
                            return parse_sample(frame)
                        except ProtocolError as error:
                            raise ThermopulpProtocolError(str(error)) from error
                self._read_into_inbox(deadline)

    def samples(
        self,
        *,
        count: int | None = None,
        timeout: float | None = None,
        stop_on_exit: bool = True,
    ) -> Iterator[Sample]:
        """Yield samples, starting and optionally stopping streaming automatically."""

        if count is not None and count < 0:
            raise ValueError("count cannot be negative")
        if count == 0:
            return
        started_here = not self._streaming
        if started_here:
            self.start_streaming(timeout=timeout)
        emitted = 0
        try:
            while count is None or emitted < count:
                yield self.read_sample(timeout=timeout)
                emitted += 1
        finally:
            if stop_on_exit and started_here and self.connected:
                try:
                    self.stop_streaming(timeout=timeout)
                except ThermopulpError:
                    pass

    def read_samples(
        self, count: int, *, timeout: float | None = None
    ) -> list[Sample]:
        """Return a fixed number of samples as a list."""

        return list(self.samples(count=count, timeout=timeout))

    def _resolve_port(self) -> str:
        if isinstance(self._requested_port, DeviceDescriptor):
            return self._requested_port.port
        if isinstance(self._requested_port, str) and self._requested_port.strip():
            return self._requested_port
        devices = discover_devices()
        if not devices:
            raise ThermopulpConnectionError(
                "No Thermopulp device found; pass an explicit serial port if needed"
            )
        if len(devices) > 1:
            ports = ", ".join(device.port for device in devices)
            raise ThermopulpConnectionError(
                f"Multiple Thermopulp devices found ({ports}); select a port explicitly"
            )
        return devices[0].port

    def _next_sequence(self) -> int:
        self._sequence = (self._sequence + 1) & 0xFFFF
        if self._sequence == 0:
            self._sequence = 1
        return self._sequence

    def _request(
        self,
        request_type: MessageType,
        response_type: MessageType,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
    ) -> Frame:
        effective_timeout = self._effective_timeout(timeout)
        sequence = self._send(request_type, payload)
        return self._wait_for_response(sequence, response_type, effective_timeout)

    def _command(
        self,
        request_type: MessageType,
        payload: bytes = b"",
        *,
        timeout: float | None = None,
    ) -> None:
        frame = self._request(
            request_type, MessageType.ACK, payload, timeout=timeout
        )
        if len(frame.payload) != 1 or frame.payload[0] != request_type:
            raise ThermopulpProtocolError("ACK does not match the command")

    def _send(self, message_type: MessageType, payload: bytes) -> int:
        transport = self._require_connected()
        sequence = self._next_sequence()
        encoded = encode_frame(message_type, sequence, payload)
        try:
            written = transport.write(encoded)
            transport.flush()
        except (OSError, serial.SerialException) as error:
            raise ThermopulpConnectionError(f"Serial write failed: {error}") from error
        if written != len(encoded):
            raise ThermopulpConnectionError("Serial write was incomplete")
        return sequence

    def _wait_for_response(
        self,
        sequence: int,
        response_type: MessageType,
        timeout: float | None,
    ) -> Frame:
        deadline = time.monotonic() + self._effective_timeout(timeout)
        while True:
            for index, frame in enumerate(self._inbox):
                if frame.sequence != sequence:
                    continue
                if frame.message_type == MessageType.ERROR:
                    del self._inbox[index]
                    self._raise_device_error(frame)
                if frame.message_type == response_type:
                    del self._inbox[index]
                    return frame
            self._read_into_inbox(deadline)

    def _read_into_inbox(self, deadline: float) -> None:
        transport = self._require_connected()
        if time.monotonic() >= deadline:
            raise ThermopulpTimeoutError("Timed out waiting for the device")
        try:
            waiting = transport.in_waiting
            data = transport.read(max(1, waiting))
        except (OSError, serial.SerialException) as error:
            raise ThermopulpConnectionError(f"Serial read failed: {error}") from error
        if not data:
            return
        frames, errors = self._decoder.feed(data)
        if errors:
            raise ThermopulpProtocolError(errors[0])
        for frame in frames:
            if frame.version != PROTOCOL_VERSION:
                raise ThermopulpProtocolError(
                    f"Unsupported device protocol version {frame.version}"
                )
            self._inbox.append(frame)

    @staticmethod
    def _raise_device_error(frame: Frame) -> None:
        if len(frame.payload) != 2:
            raise ThermopulpProtocolError("Invalid ERROR payload")
        raise DeviceRejectedError(frame.payload[0], frame.payload[1])

    def _effective_timeout(self, timeout: float | None) -> float:
        if timeout is None:
            return self.timeout
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive finite number")
        return float(timeout)

    def _discard_queued_samples(self) -> None:
        self._inbox = deque(
            frame
            for frame in self._inbox
            if frame.message_type != MessageType.SAMPLE
        )

    def _require_connected(self) -> serial.Serial:
        if not self.connected or self._serial is None:
            raise ThermopulpConnectionError("Device is not connected")
        return self._serial

    def _close_transport(self) -> None:
        transport = self._serial
        self._serial = None
        self._streaming = False
        self._decoder.reset()
        self._inbox.clear()
        if transport is not None:
            try:
                transport.close()
            except (OSError, serial.SerialException):
                pass
