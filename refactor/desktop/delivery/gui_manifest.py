"""Build-time PE dependency closure for the protected first-frame Qt shell.

Uses the existing build environment's pefile, never a runtime dependency or
an extension-only exemption. The generated control file is manifest-covered.
"""
from pathlib import Path
import json
import os
from refactor.desktop.delivery.gui_bootstrap_trust import REQUIRED
from refactor.desktop.delivery.gui_bootstrap_trust import CLOSURE
from refactor.desktop.delivery.gui_bootstrap_trust import closure_paths


def collect_closure(package):
    import pefile
    package = Path(package)
    natives = {}
    for path in package.rglob('*'):
        if path.is_file() and path.suffix.lower() in {'.dll', '.pyd', '.exe'}:
            natives.setdefault(path.name.casefold(), []).append(path)
    seeds = set(REQUIRED)
    seeds.update(path.relative_to(package).as_posix() for path in (package / '_internal').glob('*.pyd'))
    seeds.update(path.relative_to(package).as_posix() for path in (package / '_internal').glob('python*.dll'))
    seeds.update(path.relative_to(package).as_posix() for path in (package / '_internal/shiboken6').glob('*.pyd'))
    seeds.update(path.relative_to(package).as_posix() for path in (package / '_internal/shiboken6').glob('*.dll'))
    seeds.update(path.relative_to(package).as_posix() for path in (package / '_internal/PySide6/plugins/platforms').iterdir())
    for key in ('qt.conf', '_internal/qt.conf', '_internal/PySide6/qt.conf',
                '_internal/PySide6/opengl32sw.dll', '_internal/PySide6/Qt6OpenGL.dll'):
        if (package / key).is_file():
            seeds.add(key)
    pending = list(seeds)
    seen = set()
    while pending:
        key = pending.pop()
        if key in seen:
            continue
        path = package / key
        if not path.is_file():
            raise ValueError('Missing bootstrap input: ' + key)
        seen.add(key)
        if path.suffix.lower() not in {'.exe', '.dll', '.pyd'}:
            continue
        pe = pefile.PE(str(path), fast_load=True)
        try:
            pe.parse_data_directories(directories=[pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_IMPORT'], pefile.DIRECTORY_ENTRY['IMAGE_DIRECTORY_ENTRY_DELAY_IMPORT']])
            for entry in list(getattr(pe, 'DIRECTORY_ENTRY_IMPORT', [])) + list(getattr(pe, 'DIRECTORY_ENTRY_DELAY_IMPORT', [])):
                name = entry.dll.decode('ascii').casefold()
                matches = natives.get(name, [])
                if matches:
                    # Duplicate basenames can participate in Windows loaded-
                    # module/search order: verify every packaged candidate.
                    pending.extend(item.relative_to(package).as_posix() for item in matches)
                elif not name.startswith(('api-ms-win-', 'ext-ms-win-')) and not (Path(os.environ['SystemRoot']) / 'System32' / name).is_file():
                    raise ValueError('Unresolved bootstrap dependency: ' + name)
        finally:
            pe.close()
    document = {'schema':'mentorflow.gui-bootstrap-closure.v1', 'files':sorted(seen)}
    closure_paths(document)
    return document


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--package-root', required=True, type=Path)
    args = parser.parse_args()
    document = collect_closure(args.package_root)
    destination = args.package_root / CLOSURE
    if destination.exists():
        raise ValueError('Existing bootstrap closure is preserved')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(document, stream, indent=2)
    print('GUI_BOOTSTRAP_CLOSURE_FILES=' + str(len(document['files'])))


if __name__ == '__main__':
    main()
