"""Isolated packaged UAT helpers. Never target a production installation."""
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[3]
TASK = Path(os.environ.get('MF_UAT_ROOT', 'D:/MentorFlow/.local/test-tmp/inapp102c'))
if not TASK.resolve().is_relative_to(Path('D:/MentorFlow/.local/test-tmp').resolve()):
    raise ValueError('UAT artifacts must stay inside the project test-tmp directory')
APP_ID = os.environ.get('MF_UAT_APP_ID', 'MentorFlow.UAT.fast105')
FEED = 'http://127.0.0.1:18763/updates/app/stable/stable.json'

def environment():
    for name in ('local', 'temp', 'logs', 'screenshots', 'webroot/updates/app/stable'):
        (TASK / name).mkdir(parents=True, exist_ok=True)
    os.environ.update(LOCALAPPDATA=str(TASK/'local'), TEMP=str(TASK/'temp'),
        TMP=str(TASK/'temp'), TMPDIR=str(TASK/'temp'), PYTHONDONTWRITEBYTECODE='1',
        MENTORFLOW_WEB_DEPS='D:/MentorFlow/.local/web-deps', MF_UAT_APP_ID=APP_ID)
    for key in list(os.environ):
        if key.lower().endswith('_proxy'): os.environ.pop(key)
    return os.environ.copy()
