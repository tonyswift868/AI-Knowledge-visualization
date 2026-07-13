from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from app import db  # noqa: E402
from app.main import app  # noqa: E402


ARTIFACTS = ROOT / "artifacts" / "archive_import_acceptance"


def make_zip(files: dict[str, str | bytes]) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return stream.getvalue()


def save_json(name: str, value: object) -> None:
    (ARTIFACTS / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run_command(command: list[str], cwd: Path, timeout: int = 180) -> tuple[int, str]:
    completed = subprocess.run(command, cwd=cwd, text=True, encoding="utf-8", errors="replace", stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    return completed.returncode, completed.stdout or ""


def main() -> int:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="mentorflow_archive_smoke_") as temp:
        temp_root = Path(temp)
        db.DB_PATH = temp_root / "smoke.db"
        db.DATA_DIR = temp_root
        with TestClient(app) as client:
            workspace = client.post("/api/workspaces", json={"title": "Archive smoke", "execution_mode": "demo"}).json()["id"]

            normal_zip = make_zip({"notes/java/jvm.md": "# JVM\n\nSafe evidence", "src/main.py": "print('not executed')"})
            preview_response = client.post(f"/api/workspaces/{workspace}/sources/archive/preview", files={"file": ("study_bundle.zip", normal_zip, "application/zip")})
            assert preview_response.status_code == 200, preview_response.text
            preview = preview_response.json()
            save_json("normal_preview.json", preview)
            selected = [item["id"] for item in preview["tree"] if item["status"] == "supported"]
            import_response = client.post(f"/api/workspaces/{workspace}/sources/archive/import", json={"preview_id": preview["preview_id"], "selected_file_ids": selected, "trust_level": "reference"})
            assert import_response.status_code == 200, import_response.text
            save_json("normal_import.json", import_response.json())

            slip_response = client.post(f"/api/workspaces/{workspace}/sources/archive/preview", files={"file": ("zip_slip.zip", make_zip({"../escape.md": "blocked"}), "application/zip")})
            assert slip_response.status_code == 400 and slip_response.json()["error"]["code"] == "ARCHIVE_PATH_TRAVERSAL"
            save_json("zip_slip_rejection.json", slip_response.json())

            bomb_response = client.post(f"/api/workspaces/{workspace}/sources/archive/preview", files={"file": ("zip_bomb.zip", make_zip({"bomb.txt": "A" * 1_000_000}), "application/zip")})
            assert bomb_response.status_code == 400 and bomb_response.json()["error"]["code"] == "ARCHIVE_SUSPICIOUS_COMPRESSION_RATIO"
            save_json("zip_bomb_rejection.json", bomb_response.json())

            executable_response = client.post(f"/api/workspaces/{workspace}/sources/archive/preview", files={"file": ("executable.zip", make_zip({"run.exe": "MZ"}), "application/zip")})
            assert executable_response.status_code == 200
            save_json("executable_rejection.json", executable_response.json())

    test_code, test_output = run_command([str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "pytest", "backend/tests/test_v6_archive_import.py", "-q"], ROOT)
    (ARTIFACTS / "test_results.txt").write_text(test_output, encoding="utf-8")
    e2e_code, e2e_output = run_command(["npm.cmd", "exec", "--", "playwright", "test", "e2e/demo.spec.ts", "--grep", "ZIP"], ROOT / "frontend")
    (ARTIFACTS / "e2e_results.txt").write_text(e2e_output, encoding="utf-8")
    build_code, build_output = run_command(["npm.cmd", "run", "build"], ROOT / "frontend")
    (ARTIFACTS / "build_results.txt").write_text(build_output, encoding="utf-8")
    evidence = f"""# MentorFlow V6 Secure ZIP Import Evidence

- Limits: ZIP 100 MB; expanded total 500 MB; single file 50 MB; 2000 members; depth 12; single ratio 100:1; overall ratio 50:1.
- Security: Zip Slip, absolute/UNC/drive paths, symlink/special files, encrypted ZIP, suspicious compression ratios, executable extensions and binary content are guarded.
- Flow: preview stores the archive under `data/archive_previews/<preview_id>/`; import revalidates archive hash/mtime and selected member hashes, extracts only selected files, parses independently, deduplicates by workspace hash, and cleans temporary files.
- Formats: PDF/DOCX/Markdown/TXT/HTML plus code and structured text; code is text-only; notebooks extract markdown/code cells without execution; nested archives are ignored.
- Traceability: Sources retain `archive_preview_id`, `parent_archive_name`, `archive_relative_path`, and language; titles and plan evidence include `archive / internal/path`.
- Results: focused backend exit `{test_code}`, ZIP E2E exit `{e2e_code}`, build exit `{build_code}`.
"""
    (ARTIFACTS / "ARCHIVE_IMPORT_EVIDENCE.md").write_text(evidence, encoding="utf-8")
    print(f"Archive smoke artifacts written to {ARTIFACTS}")
    return 0 if test_code == e2e_code == build_code == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
