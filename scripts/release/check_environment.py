"""Assert that local build inputs match the CI lock before freezing."""
import importlib.metadata as metadata
from pathlib import Path
import sys
import subprocess
import tempfile

from scripts.release.sqlite_runtime import ensure_sqlite_runtime, validate_lock


def check(node, sqlite_cache_dir=None):
    if sys.version_info[:3]!=(3,12,10):
        raise ValueError('release Python must be 3.12.10')
    if subprocess.check_output([str(node),'--version'],text=True).strip()!='v24.13.0':
        raise ValueError('release Node must be 24.13.0')
    pins=Path(__file__).with_name('requirements-build.txt').read_text().splitlines()
    count=0
    for line in pins:
        if not line or line.startswith('#'):continue
        name,version=line.split('==')
        actual=metadata.version(name)
        if actual!=version:raise ValueError(f'{name}: installed {actual}, locked {version}')
        count+=1
    # Verify the concrete archive and extracted DLL bytes, not just a version
    # string. This is the same helper called by direct/local freeze commands.
    validate_lock()
    cache = Path(sqlite_cache_dir) if sqlite_cache_dir else Path(tempfile.gettempdir()) / "mentorflow-sqlite-runtime"
    ensure_sqlite_runtime(cache)
    return count
