"""Real React rendering: CJK strong, collapsed progress and one-shot questions."""
from pathlib import Path
import subprocess
import sys

from tests.refactor.conversation37.test_conversation37_web import build


def test_classroom81_browser_contract(tmp_path):
    build(tmp_path,Path(__file__).with_name('classroom81_browser.tsx'))
    runner=Path(__file__).parents[1]/'conversation37/test_conversation37_web.py'
    result=subprocess.run([sys.executable,'-B',str(runner),'--browser',str(tmp_path)],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    assert result.returncode==0,result.stdout+result.stderr
