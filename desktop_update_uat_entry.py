"""UAT-build-only frozen probe, never selected by the production spec branch.

Normal arguments retain the actual protected GUI entry. Qualification mode loads
the real frozen Settings update card and engine but does not authenticate, open a
database, register/install a product, elevate, or claim product end-to-end health.
"""
import sys
import time

PYTHON_ENTRY_MONOTONIC = time.monotonic()

if __name__ == '__main__':
    if sys.argv[1:] == ['--qualify-fast-materialization']:
        from desktop.packaged_update_uat import fast_materialization
        raise SystemExit(fast_materialization())
    if sys.argv[1:] in (['--qualify-packaged-updater'], ['--qualify-packaged-startup']):
        from desktop.packaged_update_uat import main, startup
        if sys.argv[1:] == ['--qualify-packaged-startup']:
            raise SystemExit(startup(PYTHON_ENTRY_MONOTONIC))
        raise SystemExit(main())
    from desktop_gui_entry import main
    raise SystemExit(main())
