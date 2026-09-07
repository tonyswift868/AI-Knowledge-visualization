"""UAT-build-only frozen probe, never selected by the production spec branch.

Normal arguments retain the actual protected GUI entry. Qualification mode loads
the real frozen Settings update card and engine but does not authenticate, open a
database, register/install a product, elevate, or claim product end-to-end health.
"""
import sys

if __name__ == '__main__':
    if sys.argv[1:] == ['--qualify-packaged-updater']:
        from desktop.packaged_update_uat import main
        raise SystemExit(main())
    from desktop_gui_entry import main
    raise SystemExit(main())
