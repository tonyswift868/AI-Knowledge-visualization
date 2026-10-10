"""Signed per-user entry for the new account shell and learning window."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from refactor.desktop.delivery.startup_window import log_phase


def main():
    # Build validation runs the frozen imports/resources without a signed
    # installation or a real account. Normal startup still verifies its tree.
    if len(sys.argv) == 3 and sys.argv[1] == "--packaging-smoke-root":
        from refactor.desktop.packaging.smoke import check
        return check(Path(sys.argv[2]))
    parser = argparse.ArgumentParser()
    parser.add_argument("--install-mode", choices=["per_user"], required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--update-attempt")
    parser.add_argument("--update-nonce")
    args = parser.parse_args()
    from refactor.desktop.delivery import per_user_install as install

    root = install.target(args.install_root)
    log_phase(root, 'process_started')
    install.check_launch_files(root, executable=Path(sys.executable))
    log_phase(root, 'launch_files_checked', closure='deferred_to_background')
    from refactor.desktop.account_shell_main import main as run_shell

    return run_shell(install_root=root, update_attempt=args.update_attempt, update_nonce=args.update_nonce)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        import traceback
        import ctypes

        root = Path(sys.executable).resolve().parent.parent
        log = root / "Data/logs/startup-error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(traceback.format_exc(), encoding="utf-8")
        ctypes.windll.user32.MessageBoxW(None, "安装文件验证或启动失败。请使用可信安装包修复。\n" + str(log), "MentorFlow", 0x10)
        raise SystemExit(1)
