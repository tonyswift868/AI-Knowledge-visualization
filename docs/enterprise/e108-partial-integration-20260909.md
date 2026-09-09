# E108 partial integration checkpoint

Dispatch: `MF-E108-DISTRIBUTION-ASSEMBLY-20260908-02`.
Mode: `PARTIAL_INTEGRATION_WHILE_HTTPS_ENDPOINT_PREPARES`.
Acceptance boundary: `READY_FOR_04_FINAL_BIND`, not a final user installer.

## Exact inputs and application order

Starting assembly: `e8f0c7e16357f2e041bdb4504944164833276195`,
tree `8ba73dfacc2263d5ba725bf53dbe2f42fa51c086` (clean).

1. 07 `78abd7d85d6cdeba0a53439908ad1417126bc456`, tree
   `efc4eea09beea8d5a787ad8d64056ea449666b04`.
   Its direct parent is the required `8c717eb5cba754cf2bdc0a0394f55ef274feae83`
   repair. That repair was already admitted/applied as `eb31423c...`; it was
   not applied a second time. New core maps to `5ce4c1934fad36ccbf3b9eab93c6e3598da7e7d0`.
2. 02 `4bc33f0165bcf882f996a5c3d5b343f3ffad33b3`, tree
   `7ff9579619afb59b437f4a7ae6b037c05a10e69f`, maps to
   `70b134608b94ec0ad8d10fbdb5d4146a68f23824`.
   The exact object was fetched from its existing local office, not a branch tip.
3. 03 `e6b87529e7dbe59198623bedcb87120b23fb33d8`, tree
   `57b4ad8d608c569d35334f381c62afc2588fa4a0`, was applied directly after 02.
4. No new 04 blocked delivery or 05 endpoint source was consumed.

The external checkpoint receipt records complete mapped commit IDs, final HEAD,
tree, source receipt hashes and test artifact hashes. Owner source-freeze
documents remain historical evidence of their exact input, not assertions that
their original source hashes equal the combined assembly.

## Integration-only conflict decisions

- Retained 02's Inno Setup 7 single-shell layout, existing known-folder default,
  directory selector, theme and single `MentorFlow-Setup.exe` output contract.
  No elevation override. An existing writable parent is required; selecting
  `D:\Apps` targets `D:\Apps\MentorFlow`.
- The signed 07 core, not Inno's former flat overlay, owns App, Updater,
  transactions, repair evidence and conservative uninstall. The shell's
  classification is an explicitly untrusted UI hint. It calls core preflight
  and install before any program-tree write. Only the final MentorFlow root
  may be newly created; normal internal payload subdirectories are unchanged.
- Inno owns Maintenance and shortcuts. Both shortcut and explicit Finish
  launch use the existing `Updater/UserRuntime.exe launch --root` route.
  It calls 07's `resume_pending_on_normal_launch`; no second update journal or
  transaction state machine was introduced.
- The 03 per-user port now calls 07 `check_latest`, `download_latest` and
  `apply_and_relaunch`. The frozen controller's single-consent continuation
  saves UI state and starts apply automatically, with no second restart button.
  It neither parses assets nor downloads/transacts itself. Historical protected
  installations retain their earlier service path; E108 per-user configuration
  never falls back to it after signed-config admission fails.
- The existing deny-only session event is retained by the native coordinator
  across GUI exit. Session-ending signals defer the existing core operation;
  `apply-latest` and `update-to-latest` join the existing runtime session scope.
- App build specification retains current repository modules and 07's runtime
  dependency closure. Tests prove the selected install/update algorithms are
  AST-identical to exact 07; only dispatcher/session/build/port wiring differs.

## Feed and build boundary

`UPDATE_FEED_URL = PENDING_04` (configuration value is `None`).
The builder accepts the final HTTPS `--update-feed-url` only after 04 supplies
the authoritative endpoint and final feed contract. Missing configuration,
HTTP, local/private addresses and credential-bearing URLs fail before build
output creation. The URL is placed in the signed version configuration, not
read from an environment override.

The old user-local feed launcher is no longer a native runtime action or a
candidate build output. Feed objects and build receipts stay in private build
work, while the user output contract contains only `MentorFlow-Setup.exe`.
No final package was built, signed, published or claimed ready in this round.
Existing historical local-feed candidates were not deleted or reclassified.

05 endpoint and 04 final delivery remain external prerequisites for the next
phase. Final signing/feed/object bindings and public HTTPS behavior must be
verified together with that delivery and the eventual BASE 1.4.0/TARGET 1.4.1
artifacts; a source checkpoint does not prove that public deployment exists.

## Evidence and limits

Root's focused gates, all with zero failures/errors/skips:

- 07 core/I06 contract: 38 passed.
- 02 installer: 37 passed, including real Inno compiler runs with `--output=no`.
- 03 UI/core regression: 95 passed, offscreen synthetic widgets only.
- Final integration/port/launcher/path/UI gate: 68 passed. Counts overlap and
  must not be added as a unique test total.

Evidence XMLs are under `E:/Codex/Temp/MF-E108-A-partial-*.xml` and hash-bound in
the external checkpoint receipt. New integration tests cover direct high-level
API routing, signed inert per-user download, automatic continuation, data/current
identity preservation, session handoff, missing-feed rejection and algorithm
equivalence. Product App/installer/UAC windows launched by A: **0**.

07's separately frozen native report
`E:/Codex/Temp/MF-OneClick-20260909/one-click-e2e-20260909T082247Z.json`
hash `292c878144337fc4ce2fdedbb106afadcab59596775eb83f5cdf6bc07a1e8133`
records real packaged fixture auto-relaunch and next-launch PASS. Its compiled
source-equivalence fence binds the owner delivery. That historical private-desktop
fixture was not rerun, and is not final combined-package/public-HTTPS evidence.

No remote push, GitHub Actions, provider calls, product window execution,
server mutation, real user-data mutation, worker provisioning or new task
dispatch was performed. New Worker task authority remains Architect-only;
Integration review feedback remains enabled.
