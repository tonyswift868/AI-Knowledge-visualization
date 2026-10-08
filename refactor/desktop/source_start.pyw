"""Source-only console-free entry; launch with the shared pythonw.exe."""
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from refactor.desktop.shell_paths import DATA_ROOT

directory = DATA_ROOT / 'logs'
directory.mkdir(parents=True, exist_ok=True)
target = directory / 'source-stderr.log'
if target.exists() and target.stat().st_size > 5 * 1024 * 1024:
    os.replace(target, directory / 'source-stderr.previous.log')
with target.open('a', encoding='utf-8', buffering=1) as output:
    sys.stdout = sys.stderr = output
    # Native Qt/Chromium diagnostics also use the same open descriptor.
    for descriptor in (1, 2):
        try: os.dup2(output.fileno(), descriptor)
        except OSError: pass  # pythonw may not have inherited CRT descriptors.
    from refactor.desktop.account_shell_main import main
    raise SystemExit(main())
