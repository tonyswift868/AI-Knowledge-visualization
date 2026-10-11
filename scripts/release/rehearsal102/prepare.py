"""Generate disposable local-only keys/CA; adapt retained UIA helper."""
from common import TASK, ROOT, environment
from pathlib import Path
environment()
if (TASK/'test-private.bin').exists(): raise ValueError('existing UAT signing key preserved')
text=(Path(__file__).with_name('inputs.py')).read_text(encoding='utf8')
exec(compile(text,'local-test-inputs','exec'))
