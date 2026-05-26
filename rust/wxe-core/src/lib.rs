//! Rust kernels for wxextract's hot paths.
//!
//! Currently exposes:
//!   * `verify_enc_key(enc_key: bytes, page1: bytes) -> bool`
//!
//! Roadmap:
//!   * `decrypt_page(enc_key, page, pgno) -> bytes`  (AES-256-CBC)
//!   * `derive_mac_key(enc_key, salt) -> bytes`     (PBKDF2-HMAC-SHA512)
//!
//! Build (separate from the main wheel):
//!   cd rust/wxe-core
//!   maturin develop --release
//! then in Python:
//!   from wxextract.keys import verify_enc_key
//!   from wxe_core import verify_enc_key as fast_verify
//!   assert fast_verify(key, page1) == verify_enc_key(key, page1)

use hmac::{Hmac, Mac};
use pbkdf2::pbkdf2_hmac_array;
use pyo3::prelude::*;
use pyo3::types::PyBytes;
use sha2::Sha512;
use subtle::ConstantTimeEq;

const PAGE_SZ: usize = 4096;
const KEY_SZ: usize = 32;
const SALT_SZ: usize = 16;
const HMAC_SZ: usize = 64;
const RESERVE_SZ: usize = 80; // IV(16) + HMAC(64)

type HmacSha512 = Hmac<Sha512>;

/// SQLCipher-4-compatible page-1 HMAC validation.
///
/// Returns true iff `enc_key` is the correct 32-byte AES key for the
/// SQLCipher database whose first page is `page1` (must be exactly
/// 4096 bytes).
#[pyfunction]
fn verify_enc_key(py: Python<'_>, enc_key: &Bound<'_, PyBytes>, page1: &Bound<'_, PyBytes>) -> PyResult<bool> {
    py.allow_threads(|| {
        let enc_key = enc_key.as_bytes();
        let page1 = page1.as_bytes();
        if enc_key.len() != KEY_SZ || page1.len() < PAGE_SZ {
            return Ok(false);
        }
        // mac_salt = salt XOR 0x3a per byte
        let salt = &page1[..SALT_SZ];
        let mut mac_salt = [0u8; SALT_SZ];
        for (i, b) in salt.iter().enumerate() {
            mac_salt[i] = b ^ 0x3a;
        }
        // mac_key = PBKDF2-HMAC-SHA512(enc_key, mac_salt, iter=2, dklen=KEY_SZ)
        let mac_key: [u8; KEY_SZ] =
            pbkdf2_hmac_array::<Sha512, KEY_SZ>(enc_key, &mac_salt, 2);
        // hmac_data = page1[16 .. PAGE_SZ - RESERVE_SZ + 16] (= body + IV)
        let hmac_data = &page1[SALT_SZ..(PAGE_SZ - RESERVE_SZ + 16)];
        let stored = &page1[PAGE_SZ - HMAC_SZ..PAGE_SZ];
        // HMAC-SHA512(mac_key, hmac_data || u32_le(1))
        let mut mac = HmacSha512::new_from_slice(&mac_key)
            .map_err(|e| pyo3::exceptions::PyValueError::new_err(e.to_string()))?;
        mac.update(hmac_data);
        mac.update(&1u32.to_le_bytes());
        let computed = mac.finalize().into_bytes();
        Ok(computed.as_slice().ct_eq(stored).into())
    })
}

#[pymodule]
fn wxe_core(_py: Python<'_>, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(verify_enc_key, m)?)?;
    m.add("__version__", "0.1.0")?;
    Ok(())
}
