"""Public Python API for Thermopalp temperature devices."""

from .protocol import (
    ChannelCalibration,
    DeviceInfo,
    DeviceSettings,
    Sample,
)

from .device import (
    DeviceDescriptor,
    DeviceRejectedError,
    ThermopalpConnectionError,
    ThermopalpDevice,
    ThermopalpError,
    ThermopalpProtocolError,
    ThermopalpTimeoutError,
    discover_devices,
)

__version__ = "0.1.0"

__all__ = [
    "ChannelCalibration",
    "DeviceDescriptor",
    "DeviceInfo",
    "DeviceRejectedError",
    "DeviceSettings",
    "Sample",
    "ThermopalpConnectionError",
    "ThermopalpDevice",
    "ThermopalpError",
    "ThermopalpProtocolError",
    "ThermopalpTimeoutError",
    "__version__",
    "discover_devices",
]
