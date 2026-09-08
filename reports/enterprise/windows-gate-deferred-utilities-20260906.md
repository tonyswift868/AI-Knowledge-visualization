# E107 defer database/update-only imports — PARTIAL

Dispatch MF-E107-STARTUP-TRUST-BOUNDARY-20260906-01.
Task MENTORFLOW_WINDOWS_FAST_START_VERIFICATION_V1.
Base f3428e8126253a1b048a2e2c0e3e22e430dcb61c.

verify_windows_package previously imported sqlite3 and urllib.request/error at
module load although ordinary installed gate verification uses neither database
checking nor release downloading. Moved those imports into check_sqlite_root,
_fresh_install_report, _github_release and stage_update. Removed the unused eager
urllib.parse import. All function bodies, read-only database URLs, update limits,
TLS defaults, digests and fail-closed checks remain. No dependency removed from
packaging; PyInstaller can still discover the imports inside their functions.
The updater test now patches the standard urllib.request module directly instead
of relying on a verifier implementation-detail attribute.

Single fresh-process importtime diagnostics: verifier cumulative 161226us before,
101735us after. Before: sqlite3 7416us, urllib.request 42079us. These instrumented
single samples are NOT a startup benchmark or a native speedup qualification.
An isolated-process regression establishes that importing the verifier no longer
loads sqlite3/_sqlite3/urllib.request/ssl. Explicit read-only database validation
still works on a synthetic SQLite file and leaves its bytes unchanged.

Initial expanded suite failed only in new test fixture cleanup: SQLite's context
manager commits but does not close the connection, causing Windows file-lock
cleanup failure. The test now explicitly closes it. Final full selected suite:
61 PASS in 8.862s. Four focused desktop updater tests PASS in 0.27s, with all data
and database environment roots set to a fresh E:/Codex/Temp directory before
collection, and offscreen isolated QSettings. No account-wide suite run. Historical
four WinError 1314 skips not rerun/promoted. Synthetic DB creation is test-only,
not a formal data write; production database-check behavior was not changed.

No native helper/final product rebuilt this turn. Earlier native runs
3.652612/2.653088/2.629414s retain their scope, not timings of this new source.
Form-interactive time remains unverified. No push, dependency/lock install/change,
server mutation or formal database write. Current result PARTIAL_RETURNED.
