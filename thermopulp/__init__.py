"""Public Python API for Thermopulp temperature devices."""

from .protocol import (
    ChannelCalibration,
    DeviceInfo,
    DeviceSettings,
    Sample,
)

from .device import (
    DeviceDescriptor,
    DeviceRejectedError,
    ThermopulpConnectionError,
    ThermopulpDevice,
    ThermopulpError,
    ThermopulpProtocolError,
    ThermopulpTimeoutError,
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
    "ThermopulpConnectionError",
    "ThermopulpDevice",
    "ThermopulpError",
    "ThermopulpProtocolError",
    "ThermopulpTimeoutError",
    "__version__",
    "discover_devices",
]
