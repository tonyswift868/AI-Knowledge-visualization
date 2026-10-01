"""Optional third-party integration, skipped in ordinary offline acceptance."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(not os.getenv('MENTORFLOW_PREFIXCASH_PYTHON'), reason='explicit isolated prefixcash environment required')
def test_prefixcash_real_fields_and_word_positions(tmp_path):
    # No paid calls: this validates the real installed tool on two synthetic rows.
    script = '''from prefixcash import to_metrics,build_heatmap,CallRecord
from prefixcash.diagnose.calls import prompt_from_messages
usage={'input_tokens':20,'cache_read_input_tokens':100,'output_tokens':5}
wrong=to_metrics('deepseek','deepseek-flash',usage)
assert wrong.input_tokens==0 and wrong.cache_read_tokens==0
right=to_metrics('anthropic','deepseek-flash',usage)
assert right.input_tokens==120 and right.cache_read_tokens==100
a=CallRecord(right,'fixed module old')
b=CallRecord(right,'fixed module new')
h=build_heatmap('x',[a,b])
assert h.first_break==2 and h.cached_ratio==2/3
print('native parser mismatch reproduced; Anthropic counts and heatmap verified')
'''
    path=tmp_path/'check_prefixcash_fields.py'
    path.write_text(script,encoding='utf-8')
    result=subprocess.run([os.environ['MENTORFLOW_PREFIXCASH_PYTHON'],str(path)],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stdout+result.stderr
