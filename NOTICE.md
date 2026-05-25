# Notices & credits

## Inspiration

The algorithm to scan a running WeChat process's memory for cached SQLCipher
encryption keys was first published (to our knowledge in the form we use) by:

- **[L1en2407/wechat-decrypt](https://github.com/L1en2407/wechat-decrypt)** — Python implementation supporting Windows, macOS, and Linux. We studied this repo to confirm:
  - the `x'<hex>'` pattern WCDB caches in process memory,
  - the PBKDF2/HMAC parameters for SQLCipher 4 on WeChat 4.x,
  - the per-page AES-256-CBC decrypt layout (reserve=80, IV at `[4032:4048]`, HMAC at `[4032:]`).

  At the time of writing the upstream repo does **not** carry a `LICENSE`
  file, so we did not vendor any verbatim code. The `wxextract` code is a
  ground-up re-implementation from those publicly observable parameters and
  the published SQLCipher 4 specification.

## References

- **SQLCipher 4** — <https://www.zetetic.net/sqlcipher/sqlcipher-api/>
  Page-1 HMAC algorithm, AES-256-CBC parameters, PBKDF2 iteration counts.

- **WCDB (Tencent)** — <https://github.com/Tencent/wcdb>
  The wrapper WeChat uses; documents the per-DB key caching format that
  makes process-memory scanning practical.

## Differences from L1en2407

- Native Python implementation, no shell-out, no Go/C dependencies.
- Hot-region prioritization: memory regions mapped from `lib*wcdb*`,
  `lib*sqlcipher*`, or `lib*wechat*` shared objects are scanned first.
- Removed the over-conservative `CAP_SYS_PTRACE` capability gate — same-UID
  `/proc/<pid>/mem` reads succeed under `kernel.yama.ptrace_scope=1` for
  descendant processes; if a read does fail the syscall surfaces the error
  directly.
- Parallel per-DB decryption via `ProcessPoolExecutor`.
- Incremental decrypt: skip files whose plaintext copy is newer than the source.

## Trademarks

WeChat and WeChat 4.0 are trademarks of their respective owners. This tool
is not affiliated with Tencent.

## Disclaimer

`wxextract` reads only the files and process memory of WeChat instances owned
by the user running the tool. It is intended for personal data export (your
own conversations, on your own machine). Use responsibly.
