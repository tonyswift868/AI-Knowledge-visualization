"""Runtime contracts extracted from scripts/build_inno_installer.py."""
from __future__ import annotations

import re

def collect_payload(payload):
    files, directories = [], []
    for path in sorted(payload.rglob('*')):
        if path.is_symlink() or path.is_junction():
            raise ValueError(f'Payload links are forbidden: {path}')
        rel = str(path.relative_to(payload))
        if any(c in rel for c in '\n\r";='):
            raise ValueError('Unsupported payload filename')
        if path.is_dir():
            directories.append(rel)
        elif path.is_file():
            if path.parts[len(payload.parts)].lower() == 'installer' or re.fullmatch(
                    r'(?i)mentorflow.*(?:setup|candidate).*\.exe', path.name):
                raise ValueError('Payload must not contain installer candidates')
            if path.name.lower() == '.mentorflow-install.ini':
                raise ValueError('Input must not contain a previous shell ownership record')
            files.append((rel, path.stat().st_size))
    return files, directories
