"""Developer entry point and explicit receipt/projection producer commands."""
import argparse
import json
from pathlib import Path
import sys
import traceback

from .authority import RepoSource, export_projection, load_view, receipt_to_manifest
from .targets import Refused, TrustedPaths, json_bytes, read_checked

RUNTIME = Path('E:/Codex/Coordination/MentorFlow/MAIN3/dev-workbench')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', action='store_true', help='Rebuild disposable projection; never alters repo authority')
    parser.add_argument('--output', type=Path, default=RUNTIME)
    parser.add_argument('--prepare-receipt', type=Path, help='Normalize one public receipt; does not approve or launch it')
    parser.add_argument('--test-id')
    args = parser.parse_args()
    try:
        if args.prepare_receipt:
            if not args.test_id: parser.error('--test-id is required with --prepare-receipt')
            manifest = receipt_to_manifest(json_bytes(read_checked(args.prepare_receipt, TrustedPaths())), args.test_id)
            args.output.mkdir(parents=True, exist_ok=True)
            (args.output / 'TEST_TARGET_DRAFT.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
            print('DRAFT_CREATED; repo approval still required')
            return 0
        if args.export:
            export_projection(load_view(RepoSource()), args.output)
            print('PROJECTION_REBUILT')
            return 0
        from PySide6.QtWidgets import QApplication
        from .window import Workbench
        app = QApplication(sys.argv[:1])
        app.setApplicationName('MentorFlow Dev Workbench')
        window = Workbench(); window.show()
        return app.exec()
    except Refused as error:
        print(str(error), file=sys.stderr)
        return 2
    except Exception:
        traceback.print_exc(file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
