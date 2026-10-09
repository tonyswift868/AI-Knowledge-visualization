"""Assert that local build inputs match the CI lock before freezing."""
import importlib.metadata as metadata
from pathlib import Path
import sys
import subprocess


def check(node):
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
    return count
