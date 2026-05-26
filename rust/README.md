# Rust hot-path kernels (optional accelerator)

`wxe-core` is a PyO3 + maturin extension module that reimplements
wxextract's CPU-heaviest functions in Rust. Today it ships one
function — `verify_enc_key` — as a proof of concept; the AES-CBC
per-page decrypt and PBKDF2 derivation are the obvious next ports.

## Status

| Function | Pure-Python | Rust | Auto-wired in `wxextract.keys` |
|---|---|---|---|
| `verify_enc_key` | ✅ | ✅ (PoC) | ❌ (manual import for now) |
| `derive_mac_key` | ✅ | — | — |
| `_decrypt_page`  | ✅ | — | — |

## Build

```sh
# one-time toolchain setup
rustup toolchain install stable
pip install maturin

# develop-install into the active venv
cd rust/wxe-core
maturin develop --release
```

That produces a `wxe_core` module importable from Python:

```python
from wxe_core import verify_enc_key
# matches wxextract.keys.verify_enc_key exactly, but ~5-10× faster on big scans
```

## Why this isn't required

`pycryptodome` already calls into compiled C for the AES + HMAC + PBKDF2
primitives, so the existing pure-Python path is mostly memcpy + Python
dispatch overhead. The Rust path mainly wins when scanning ten-thousand
candidate keys against many DB headers in a tight loop — the typical
WeChat install only has ~20 DBs, so the wall-clock saving on a single
run is small.

The wins compound for:
1. Sustained scanning (e.g. recovering keys for many WeChat accounts in
   batch on a server).
2. Future image-key V2 recovery from process memory, which DOES burn
   through many MB of memory regions.
3. Replacing the `_decrypt_page` loop where pycryptodome's overhead
   per-call matters for tens of thousands of pages.

## Integration plan (not yet wired)

```python
# wxextract/keys.py would gain:
try:
    from wxe_core import verify_enc_key as _verify_rust
    verify_enc_key = _verify_rust
except ImportError:
    pass  # keep the pure-Python verify_enc_key
```

That keeps `pip install wxextract` working without a Rust toolchain,
while users who run `maturin develop` (or install a wheel published from
the Rust crate) get the speedup transparently.

## Building a wheel for distribution

```sh
cd rust/wxe-core
maturin build --release --strip
# wheel lands in target/wheels/
```

Publish separately to PyPI as `wxe-core`. Or bundle into the wxextract
wheel by switching wxextract's build-backend from `hatchling` to
`maturin` and merging the crates — bigger commitment, deferred.
