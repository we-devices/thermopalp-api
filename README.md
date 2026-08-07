# Thermopulp Python API

An open-source Python SDK for discovering, configuring, and streaming samples
from Thermopulp USB temperature devices. It uses the device's compact binary
protocol over USB CDC and does not require the desktop application or Qt.

## Installation

```console
python -m pip install thermopulp
```

Python 3.10 or newer is supported on Windows, Linux, and macOS.

## Quick start

```python
from thermopulp import ThermopulpDevice

with ThermopulpDevice() as device:
    print(device.device_info)
    for sample in device.samples(count=10):
        print(sample.uptime_ms, sample.temperatures_c)
```

When exactly one Thermopulp is connected, the API selects it automatically.
Pass a serial port such as `COM21` or `/dev/ttyACM0` to `ThermopulpDevice` when
you need explicit selection.

The API also supports device discovery, sample interval and channel settings,
and persistent per-channel calibration. See the
[API guide](https://github.com/Hvunt/thermopulp-api/blob/main/docs/python-api.md)
and
[complete example](https://github.com/Hvunt/thermopulp-api/blob/main/examples/read_temperatures.py).

The desktop UI and API cannot own the same serial port simultaneously. Close
or disconnect the UI before opening the device through Python.

## Development

```console
python -m venv .venv
python -m pip install -e ".[dev]"
python -m unittest discover -s tests -v
python -m build
python -m twine check --strict dist/*
```

## License

Thermopulp Python API is released under the
[MIT License](https://github.com/Hvunt/thermopulp-api/blob/main/LICENSE).
