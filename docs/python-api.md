# Thermopulp Python API

The `thermopulp` package provides synchronous access to a Thermopulp USB CDC
device without launching the desktop UI or creating a Qt event loop. It works
on Windows, Linux, and macOS through pyserial.

## Connect and stream

```python
from thermopulp import ThermopulpDevice

with ThermopulpDevice("COM21") as device:
    print(device.device_info)
    print(device.settings)

    for sample in device.samples(count=100):
        for channel, temperature in enumerate(sample.temperatures_c):
            enabled = bool(sample.channel_mask & (1 << channel))
            if enabled and sample.statuses[channel] == 0:
                print(channel + 1, temperature)
```

When no port is supplied, `connect()` auto-selects the device only if discovery
finds exactly one Thermopulp. An explicit port is required when multiple devices
are connected.

The constructor does not access hardware. `connect()` opens the port, clears
stale input, stops any old streaming session, and reads `device_info` and
`settings`. `close()` stops API-owned streaming when possible. The context
manager always closes the serial handle.

## Discovery

```python
from thermopulp import discover_devices

for device in discover_devices():
    print(device.port, device.serial_number, device.display_name)
```

`discover_devices()` filters by the Thermopulp name or its current USB VID/PID.
Pass `include_all_serial=True` when diagnosing a device with custom USB IDs.

## Streaming methods

| Method | Behavior |
|---|---|
| `start_streaming()` | Starts acquisition and waits for the matching ACK |
| `read_sample()` | Returns one `Sample`; streaming must already be active |
| `samples(count=None)` | Starts when needed and yields `Sample` objects |
| `read_samples(count)` | Returns a fixed-size list of samples |
| `stop_streaming()` | Stops acquisition, waits for ACK, and drops queued stale samples |

The timeout applies to each command response or sample. Device samples contain
device uptime, channel mask, four temperatures, four status values, and two
cold-junction temperatures. Temperatures are returned in degrees Celsius.

## Settings and calibration

```python
with ThermopulpDevice() as device:
    device.set_sample_interval(1000)
    device.set_channel_mask(0b0101)  # Channels 1 and 3
    device.set_calibration(0, gain_ppm=1_001_250, offset_millidegrees=-300)
    device.reset_calibration(0)
    device.reset_calibration()       # All channels
```

Every settings method waits for the command ACK and then reads settings back
from the device. Channel indexes in calibration methods are zero-based. A
channel mask must enable at least one of bits 0 through 3.

Firmware update remains in the desktop UI because its safe workflow includes
package validation, calibration backup, RDP handling, reconnect, restore, and
verification.

## Errors

All API exceptions derive from `ThermopulpError`:

- `ThermopulpConnectionError`: discovery, open, read, or write failure
- `ThermopulpTimeoutError`: expected response or sample was not received
- `ThermopulpProtocolError`: malformed, corrupt, or unsupported frame
- `DeviceRejectedError`: typed device `ERROR` response, with `request_type` and
  `error_code` attributes

The UI and API cannot own the same serial port simultaneously. Disconnect or
close the UI before opening the device from Python. Linux users may need an
appropriate udev rule or membership in the distribution's serial-port group.
