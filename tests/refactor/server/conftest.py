"""Keep legacy ``app`` imports out of the desktop suite's ``tests/refactor/app``."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from slim_sqlite_support import schema_template


_COMPARISONS = {'test_slim_http_contract.py', 'test_slim_lifecycle_contract.py',
                'test_slim_persistence_contract.py'}


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    # Both real entries and all assertions execute in the child. No mock import,
    # skipped comparison, or process-global module replacement is involved.
    if pyfuncitem.path.name not in _COMPARISONS or os.environ.get('SLIM_CONTRACT_CHILD') == '1':
        return None
    import importlib.util
    root = Path(__file__).resolve().parents[3]
    if not (root / 'backend').is_dir() or importlib.util.find_spec('docling_core') is None:
        # Side-by-side comparison with the old backend; it needs the old backend's own dependencies.
        # Passed at migration time (14-MF07); runs again wherever those dependencies are installed.
        pytest.skip('old backend or its dependencies not available')
    temporary = pyfuncitem.funcargs.get('tmp_path')
    if temporary is None:
        temporary = pyfuncitem.config._tmp_path_factory.mktemp('contract-process')
    temporary = Path(temporary)
    namespace = getattr(sys.modules.get('app'), '__file__', None)
    if namespace:
        (Path(os.environ.get('TEMP', temporary)) / 'namespace-evidence.txt').write_text(
            str(namespace), encoding='utf-8')
    env = dict(os.environ, SLIM_CONTRACT_CHILD='1', PYTHONDONTWRITEBYTECODE='1')
    root = Path(__file__).resolve().parents[3]
    # Use an explicit backend search root only inside the reference process.
    env['PYTHONPATH'] = os.pathsep.join([str(root/'backend'),str(root),env.get('PYTHONPATH','')])
    result = subprocess.run([sys.executable, '-B', '-m', 'pytest', pyfuncitem.nodeid,
        '-q', '--tb=short', '-p', 'no:cacheprovider', '--basetemp', str(temporary/'child')],
        cwd=root, env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    return True
