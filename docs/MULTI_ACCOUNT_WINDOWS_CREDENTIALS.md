# Local accounts and Windows credentials

Authority: user task MENTORFLOW_MULTI_ACCOUNT_AND_WINDOWS_CREDENTIALS,
2026-09-14. This changes only the desktop local account lifecycle. Server account
creation/deletion, verifier algorithms, issuer configuration and learning data
are outside this change.

The design uses account collection/current-selection behavior from
[Nextcloud Desktop](https://docs.nextcloud.com/server/latest/user_manual/en/desktop/usage.html)
and per-profile isolation from [Joplin](https://joplinapp.org/help/apps/profiles/).
No GPL source was copied. Windows secret storage uses the existing
[keyring WinVault backend](https://keyring.readthedocs.io/en/latest/).

## Registry and lifecycle

`AccountRegistry` stores one bounded, versioned, nonsecret JSON value in existing
QSettings. Its fields are stable account ID, issuer/subject hashes, display label,
session/password consent and auto-login preference; current_account_id selects
one entry. IDs are derived by the existing canonical account identity contract,
never by a display username. Legacy selection is retained on the first add, with
remember_password=false. Registry IDs are rederived and checked on every read;
unknown fields and malformed data are rejected, not silently overwritten.
One metadata write contains the full registry; QSettings and a named OS mutex serialize
writers. Registry selection does not authorize mounting a Vault or server access.

The login screen provides a local account list, Add account and Remove local
account. The existing main-window switch action first fences producers, drains
the API/executors and closes/unmounts the previous account before returning to
that screen. Reauthentication still passes the existing server identity and
workspace checks before bootstrap/mount. Vaults retain their existing stable
per-account roots; no duplicate profile/storage engine is introduced.

Remove local account deletes that registry entry and its session/password
credentials only. It does not call a server delete endpoint or delete a Vault,
SQLite database, cache, learning document or server learning record. Logout clears
the selected session and disables auto-login. It preserves a saved password by
default; `logout(..., forget_password=True)`, unchecking Remember password at a
successful login, or Remove local account explicitly forgets it.

## Consent and automatic login

Remember login state retains session tokens in the existing account credential
namespace. Auto-login is enabled with that session-retention consent. Remember
password is a separate, unchecked-by-default control. Only after successful
native authentication does the optional password reach SavedPasswordVault.
Form input is cleared on submission, and worker references are released. A
cancelled or superseded UI generation cannot accept/persist its login result.

Restore first uses the existing secure session/refresh path. Only a missing or
explicitly invalid session permits saved-password reauthentication, and only if
that exact account has password consent. Network errors and issuer changes do
not trigger password replay. The configured canonical issuer is checked before
reading the password. Reauthentication must return the selected stable account
ID before its result can be accepted. A label/username collision cannot switch
the credential namespace. Logout/removal serialize against account operations.

`SavedPasswordVault` uses only keyring.backends.Windows.WinVaultKeyring. Its key
is native-password:<stable-account-id>; a separate service purpose prevents
token/password collisions and preserves the existing test-device service scope.
The username/password envelope is inside the OS credential blob, never a JSON
file. No password, access token or refresh token enters registry/settings/logs.
No plaintext, environment-variable or custom encrypted-file password fallback
exists. Write/read/delete failures return static errors and do not grant login.

## Validation levels

First, a tiny Windows process and PyInstaller onefile executable exercised the
real auto-selected WinVault backend: set/get/overwrite/delete, two accounts with
one service and complete probe cleanup. The reusable
`scripts/windows_account_credentials_spike.py` additionally exercises the actual
product password adapter and stable-ID key derivation against real WinVault.
It uses a unique disposable service and emits no secret values. It can be built
with PyInstaller --onefile --exclude-module PySide6 --paths <repo>; Qt is not a
dependency of this credential-only executable. This is a frozen credential-store
qualification, not a full MentorFlow package or live-server acceptance.

Focused tests exercise actual QSettings persistence, independent Vault roots and
files, A/B selection/removal, consent and absence of plaintext in filesystem
outputs, session-first restore, saved-password fallback, network/issuer rejection,
wrong-account results, logout actions, metadata corruption/write failure, legacy
migration and the native Qt form. Authentication responses in these tests are
fixtures; no live account/server mutation or full packaged main-window UAT is
claimed by those checks.

### 2026-09-14 validation receipt

Employee 02 imported exact integrated source bcc9f2509673daccfa39ad67c5771a50d1cdfb61
into its own branch as merge 506cab1ef26223e2416b7a2f93eef3d8acfc4c1e. That merge's
tree matched the integrated snapshot, including its existing native authentication
and canonical import fixes. No integration branch or other worker was written.

Credential qualification used Python 3.13, keyring 25.7.0 and PyInstaller 6.21.0
on Windows. Both the auto-selected backend and product adapter reported
keyring.backends.Windows.WinVaultKeyring and PASS_SET_GET_OVERWRITE_DELETE_AB_ISOLATION.
The probes created only random disposable services/accounts and removed their
entries in finally blocks. All password values were generated in memory.

Frozen probe SHA256:

- Raw backend: 371c5d215504e0212cb08a38b6acb1f1bec8b92284161b1139662a54163d4177.
- Product password adapter: 0605381537f4362be20e5458674c630073eb012d195d3b5e4a7909b0cf12c1de.

Evidence directory: E:/Codex/Temp/MF-MULTI-ACCOUNT-20260914. It contains
frozen-result.json, product-source-result.json, product-frozen-result.json,
the build logs and disposable executables under dist/.

Combined regression: 136 passed across test_multi_account, test_login_smooth,
test_native_login, test_rehydration_transport, test_prelogin_update_ux and
test_account_switching. After synchronizing cancellation with per-account auto
preferences, the final multi-account/login/native follow-up passed 65 tests.
The sets overlap; these are not additive counts. Detailed logs are
final-focused.log and final-followup.log in the evidence directory.

MULTI_ACCOUNT, A_B_PROFILE_ISOLATION, AUTO_LOGIN_VALID_SESSION and
AUTO_LOGIN_SAVED_CREDENTIAL pass source/Qt fixture verification.
REMEMBER_PASSWORD passes the real Windows Credential Manager adapter check;
FROZEN_CREDENTIAL_STORE passes its real PyInstaller executable check.
UNSAVED_PASSWORD_PERSISTENCE is ZERO in the tested flows. No real user account,
learning data, existing credential entry or acceptance runtime was modified.
A full product rebuild, live-server multi-account login and installed UI UAT
were not run or claimed.
