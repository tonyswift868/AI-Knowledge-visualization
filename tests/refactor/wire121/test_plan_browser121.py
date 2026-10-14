import os
from pathlib import Path
import subprocess
import sys
from tests.refactor.focus119.test_browser_focus119 import build119


def test_121_new_plan_big_small_counts_and_navigation(tmp_path):
    build119(tmp_path,states={},entry=Path(__file__).with_name('browser121.tsx'))
    browser=Path(__file__).resolve().parents[1]/'conversation37/test_conversation37_web.py'
    result=subprocess.run([sys.executable,'-B',str(browser),'--browser',str(tmp_path)],
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=75)
    assert result.returncode==0,result.stdout+result.stderr
