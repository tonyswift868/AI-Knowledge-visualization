"""PyInstaller entry point that preserves the desktop package context."""

from desktop.main import main


if __name__ == "__main__":
    raise SystemExit(main())
