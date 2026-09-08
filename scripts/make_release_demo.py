"""Create a tiny SYNTHETIC local static-feed demo, never a product release."""
import argparse
from pathlib import Path
import subprocess
import sys

from scripts.test_build_release import package


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--origin', default='http://127.0.0.1:8766')
    args = parser.parse_args()
    root = args.root.absolute()
    root.mkdir(parents=True, exist_ok=False)
    package(root / 'BASE', '1.1.0')
    package(root / 'TARGET', '1.1.1', target=True)
    notes = root / 'notes.txt'
    notes.write_text('SYNTHETIC qualification demo. Not a shipped MentorFlow release.\n', encoding='utf-8')
    repository = Path(__file__).absolute().parent.parent
    source = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=repository, text=True).strip()
    tree = subprocess.check_output(['git', 'rev-parse', 'HEAD^{tree}'], cwd=repository, text=True).strip()
    subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-File', str(repository / 'scripts/build_release.ps1'),
        '-Version', '1.1.1', '-Channel', 'stable', '-TargetDirectory', str(root / 'TARGET'),
        '-BaseDirectory', str(root / 'BASE'), '-ReleaseNotes', str(notes), '-OutputDirectory', str(root / 'release'),
        '-Origin', args.origin, '-SourceSha', source, '-TreeSha', tree,
        '-DevelopmentSigning', '-PythonPath', sys.executable], cwd=repository, check=True)


if __name__ == '__main__':
    main()
