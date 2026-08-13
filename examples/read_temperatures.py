"""Print Thermopalp samples without starting the desktop UI."""

from __future__ import annotations

import argparse

from thermopalp import ThermopalpDevice, ThermopalpError, discover_devices


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", help="Serial port, for example COM21 or /dev/ttyACM0")
    parser.add_argument("--count", type=int, default=10, help="Number of samples")
    args = parser.parse_args()

    if args.port is None:
        devices = discover_devices()
        for device in devices:
            print(f"Found: {device.display_name}")

    try:
        with ThermopalpDevice(args.port) as device:
            info = device.device_info
            if info is not None:
                print(
                    f"Firmware {info.firmware_version}, "
                    f"hardware {info.hardware_revision}, port {device.port}"
                )
            for sample in device.samples(count=args.count):
                values = ", ".join(
                    f"CH{index + 1}={temperature:.2f} °C"
                    for index, temperature in enumerate(sample.temperatures_c)
                    if sample.channel_mask & (1 << index)
                    and sample.statuses[index] == 0
                )
                print(f"{sample.uptime_ms / 1000:9.3f} s  {values}")
    except ThermopalpError as error:
        raise SystemExit(f"Thermopalp error: {error}") from None


if __name__ == "__main__":
    main()
