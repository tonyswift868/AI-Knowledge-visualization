"""Run the real dry-run CLI on this task's isolated SQLite backup only.

The existing CLI's global process detector also rejects unrelated running app
instances on a DIFFERENT data root. This test replaces only that detector for
the verified task copy; never use this helper on a live application database.
"""
import argparse
from pathlib import Path
from unittest.mock import patch
from scripts import plan_restructure as cli
from tests.refactor.points115.run115 import ROOT,ACCOUNT,PLAN


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--today',required=True)
    args=parser.parse_args(argv)
    data_root=(ROOT/'snapshot').resolve()
    database=data_root/'learning/accounts'/ACCOUNT/'course.sqlite3'
    if not database.is_file() or not database.resolve().is_relative_to(ROOT.resolve()):
        parser.error('Only this task backup may be previewed')
    with patch.object(cli,'_running_software',return_value=[]):
        return cli.main(['--dry-run','--data-root',str(data_root),'--plan-id',PLAN,'--account-id',ACCOUNT,
                         '--structure',str(ROOT/'apply-ready.json'),'--today',args.today,
                         '--report',str(ROOT/'preview.json')])


if __name__=='__main__':raise SystemExit(main())
