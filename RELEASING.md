# Releasing Thermopulp API to PyPI

The PyPI distribution and import package are both named `thermopulp`.

## One-time Trusted Publisher setup

Create pending publishers on PyPI and TestPyPI using these values:

| Setting | PyPI | TestPyPI |
|---|---|---|
| PyPI project name | `thermopulp` | `thermopulp` |
| GitHub owner | `Hvunt` | `Hvunt` |
| Repository | `thermopulp-api` | `thermopulp-api` |
| Workflow | `publish-pypi.yml` | `publish-pypi.yml` |
| Environment | `pypi` | `testpypi` |

Create matching GitHub Environments and require manual approval for `pypi`.
The workflow uses Trusted Publishing and needs no long-lived PyPI token.

## Prepare and validate

1. Update `thermopulp.__version__` and `CHANGELOG.md`.
2. Run:

   ```console
   python -m pip install -e ".[dev]"
   python -m unittest discover -s tests -v
   python -m build
   python -m twine check --strict dist/*
   ```

3. Install the wheel in a clean virtual environment and test discovery and
   live sampling with a device.
4. Commit the release. Never reuse a version already uploaded to PyPI.

## TestPyPI

Run `Publish Python distribution` manually in GitHub Actions. Install the
dependency from PyPI and the exact API version from TestPyPI:

```console
python -m pip install "pyserial>=3.5,<4"
python -m pip install --index-url https://test.pypi.org/simple/ --no-deps \
    thermopulp==0.1.0
```

Replace `0.1.0` with the version being tested.

## Production PyPI

Create and publish a GitHub Release from the release commit or tag. The
production job waits for approval in the `pypi` GitHub Environment before it
publishes the already validated artifacts.
