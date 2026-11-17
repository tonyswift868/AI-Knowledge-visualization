from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess
import sys


REPO = Path(__file__).resolve().parents[3]
PRODUCT_ROOTS = (REPO / "refactor" / "desktop", REPO / "refactor" / "learning")
DRIVE_PATH = re.compile(r"(?<![A-Za-z])[DE]:[\\/]")
PROTECTED = {
    Path("refactor/desktop/delivery/install_product.py"),
    Path("refactor/desktop/delivery/per_user_install.py"),
    Path("refactor/desktop/delivery/update_service.py"),
}


def test_source_data_root_follows_localappdata(tmp_path):
    local_app_data = tmp_path / "LocalAppData"
    env = dict(os.environ, LOCALAPPDATA=str(local_app_data))
    result = subprocess.run(
        [sys.executable, "-c", "from refactor.desktop.shell_paths import DATA_ROOT; print(DATA_ROOT)"],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert Path(result.stdout.strip()) == local_app_data / "MentorFlow-Source"


def test_product_code_has_no_developer_drive_defaults_outside_protection_lists():
    hits = []
    for product_root in PRODUCT_ROOTS:
        for path in product_root.rglob("*"):
            if not path.is_file() or path.suffix not in {".py", ".pyw"}:
                continue
            relative = path.relative_to(REPO)
            if relative in PROTECTED:
                continue
            for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if DRIVE_PATH.search(line):
                    hits.append(f"{relative}:{line_number}:{line.strip()}")
    assert not hits, "product code contains developer drive paths:\n" + "\n".join(hits)
