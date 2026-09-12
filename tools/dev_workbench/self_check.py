"""The only V1 SCRIPT_ID. Never loads or executes a manifest-provided script."""
import re
import sys

if __name__ == '__main__':
    if len(sys.argv) != 2 or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,95}', sys.argv[1]):
        raise SystemExit(2)
    print('WORKBENCH_SELF_CHECK PASS ' + sys.argv[1])
