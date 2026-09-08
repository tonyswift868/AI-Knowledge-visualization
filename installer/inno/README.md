# MentorFlow installer shell — V2

Dispatch: `MF-E108-MODERN-INSTALLER-VISUAL-REWORK-20260908-02`.
One Inno Setup 7 user candidate; unchanged NSIS **source** is retained.
Do not build or launch a second user-facing installer. Acceptance and exact
artifact identity are recorded in `verification.md`.

## Build without launching

Windows, Python 3.12+, Pillow and the existing official Inno Setup 7.1.0 compiler
are required. No new window engine, external plugin DLL or font is bundled.

```powershell
python scripts/build_inno_installer.py --payload E:\path\to\verified\MentorFlow --output E:\Codex\Temp\MentorFlow-Inno
```

Output is `MentorFlow-Setup.exe`. The builder never runs it. The input package
must pass the existing manifest verifier with `--no-smoke --no-data-check`,
which neither starts the product nor inspects application data. Version comes
from the input package, not the checkout. Output inside payload is rejected.
Build inputs, receipts and compiler logs stay in E: temporary storage.

Final user folder: `E:\Downloads\MentorFlow-UAT-Current\Installer`.
Confirm and recoverably archive old MentorFlow UAT installer EXEs before a new
candidate build. Do not remove unrelated files. Copy only one final product
EXE and small evidence there; never publish the inert UX fixture.

## Design and engine boundary

- `modern dynamic windows11 hidebevels includetitlebar` follows Windows at startup.
- The official icon is copied unchanged from
  `frontend/public/teacher2d/tsubaki-live2d/icon.png`, also used by
  `scripts/build_desktop_icon.py`. Colors derive from
  `frontend/src/styles/warm-theme.css`; muted text is darkened for card contrast.
- The three states share a 720 × 452 design-pixel client surface: icon and
  wordmark, prominent title, quiet subtitle and one task. No large sidebar.
- `CreateCustomPage`, native labels, stock folder icon, progress bar and buttons
  provide the UI. Inno's official accessible `TBitmapButton` supplies primary
  image/focus behavior; Caption supplies its accessibility name. Original Inno
  next/cancel handlers retain installation and finalization ownership.
- Geometry uses the form font PPI via `MulDiv`; text uses Segoe UI and Windows
  fallback. The official PNG is kept at source resolution.
- Location shows final destination and payload + 256 MiB reserve. Missing
  parent/drive, reparse paths, unknown nonempty targets, insufficient space and
  missing write access block Install with inline Chinese text.
- Browse independently revalidates the native picker's result. In Inno 7 its
  last argument is **reserved**, not a create-folder flag. The shell never
  creates ancestors; PrepareToInstall creates only the final MentorFlow child.
- Per-user is default. No automatic elevation or second elevated wizard;
  non-writable locations ask for another folder. Existing explicit ALLUSERS
  mode is not a new distribution contract.
- Progress comes from CurInstallProgressChanged, not a timer. Raw paths are
  hidden unless Details is expanded; visible history is bounded to 100 lines.
  Inno retains its full setup log.
- Finish has **启动 MentorFlow** and **关闭**. Run defaults unchecked; only the
  explicit launch action checks it. Launch remains as original user.
- Unexpected engine/error pages are not concealed by the custom surface.
- SetupMutex prevents concurrent progression of copies of this Inno wizard.
  It does not retroactively control legacy NSIS.

No updater/feed/trust, account, database or server changes. The ownership record
is an isolated interface, not a claim that employee 07's eventual per-user
contract is frozen. Legacy NSIS targets are not adopted merely by filename.

## Offline and pending UI checks

These checks never execute an installer:

```powershell
python -m unittest discover -s installer/inno/tests -p test_build_contract.py -v
python -m unittest discover -s installer/inno/tests -p test_design_contract.py -v
python -m unittest discover -s installer/inno/tests -p test_native_font_metrics.py -v
python scripts/build_inno_installer.py --fixture --output E:\Codex\Temp\MentorFlow-CompileOnly
```

Fixture is an inert temporary payload, never a product artifact.
test_compiler_profiles also contains a compile-only production-sections check.
The other runtime-policy tests **execute fixture installers** with a pre-wizard
UXPROBE exit; do not run these under the current no-launch limit.

Geometry tests inspect source bounds at virtual 100/125/150/200% metrics.
Native font tests use an in-memory Win32 device context at those four metrics;
they create no window and change no process or system DPI setting. They measure
static copy/titles/buttons, not dynamic long paths or actual VCL rendering.
Contrast tests check actual palette pairs. Neither proves native rendering,
text clipping, keyboard behavior, installation or launch. V1 results do not
prove V2 behavior.

Keep host scaling at 100%. Do not change Windows theme/scaling or install a VM.
The later user objection paused installer UI operations. An explicitly agreed
single-instance acceptance session or approved isolated environment remains
necessary for the native screenshots and runtime checks. Do not substitute
browser mocks, simulated screenshots or screenshots from another binary.

## Official references

- [WizardStyle](https://jrsoftware.org/ishelp/topic_setup_wizardstyle.htm)
- [Wizard backgrounds](https://jrsoftware.org/ishelp/topic_setup_wizardbackimagefile.htm)
- [Wizard image](https://jrsoftware.org/ishelp/topic_setup_wizardimagefile.htm)
- [High-resolution small image](https://jrsoftware.org/ishelp/topic_setup_wizardsmallimagefile.htm)
- [CreateCustomPage](https://jrsoftware.org/ishelp/topic_isxfunc_createcustompage.htm)
- [Native support classes](https://jrsoftware.org/ishelp/topic_scriptclasses.htm)
- [BrowseForFolder](https://jrsoftware.org/ishelp/topic_isxfunc_browseforfolder.htm)
- [SetupMutex](https://jrsoftware.org/ishelp/topic_setup_setupmutex.htm)
- Installed official Examples/CodeClasses.iss, including native bitmap button.

Unsigned UAT candidate only. The compiler reports non-commercial use only;
maintainers must settle applicable commercial licensing/signing before shipping.
