"""Native GUI-helper entry; one canonical trust module in frozen and source runs."""
from scripts.windows_install_trust import main

if __name__ == "__main__":
    raise SystemExit(main())
