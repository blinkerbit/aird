# aird-transfer

Native Rust (PyO3) acceleration for [Aird](https://github.com/blinkerbit/aird) uploads/downloads.

Imported as the top-level module `aird_transfer`. Aird loads it automatically when present (`aird.core.transfer_native`); without it, pure-Python fallbacks are used.

## Install (recommended)

```bash
pip install aird
```

Aird depends on this package. PyPI publishes **binary wheels** for common platforms (Windows / macOS / Linux, manylinux) — install is the same hassle-free path as tools like `ruff` (no Rust toolchain on the user machine).

```bash
# Or install the extension alone
pip install aird-transfer
```

## Build from source

Requires [Rust](https://rustup.rs/) (stable) and Python ≥ 3.10.

```bash
cd native/aird_transfer
pip install maturin
maturin develop --release   # editable install into the active venv
# or
maturin build --release     # wheel under target/wheels/
pip install target/wheels/aird_transfer-*.whl
```

## Verify

```python
from aird.core.transfer_native import native_available
print(native_available())  # True when the extension loaded
```
