"""Deterministic real React regression for BUG-103's initial effect race."""
from pathlib import Path
import subprocess
import sys
import json

import pytest

from tests.refactor.concept114.test_concept114_browser import build


@pytest.mark.parametrize('clock', ['2026-10-03T09:00:00+09:00', '2026-10-03T21:00:00+09:00', '2026-10-03T23:59:59+09:00'], ids=['morning','evening','midnight'])
def test_material141_first_click_before_passive_effects_is_not_discarded(tmp_path, clock):
    build(tmp_path, Path(__file__).with_name('browser141.tsx'))
    # Freeze only this owned browser. Backend snapshots already use an injected
    # study day/now in make_environment; no real clock or user process is changed.
    script = """<script>
const NativeDate141 = Date;
let clock141 = NativeDate141.parse(CLOCK);
window.advanceClock141 = () => { clock141 += 2000 };
window.Date = new Proxy(NativeDate141, {
  construct(target, args) { return Reflect.construct(target, args.length ? args : [clock141]) },
  apply() { return new NativeDate141(clock141).toString() },
  get(target, key) { return key === 'now' ? () => clock141 : Reflect.get(target, key) }
});
</script>""".replace('CLOCK', json.dumps(clock))
    index = tmp_path/'dist/index.html'
    index.write_text(script+index.read_text(encoding='utf-8'), encoding='utf-8')
    result = subprocess.run(
        [sys.executable, '-B', str(Path(__file__).with_name('test_concept114_browser.py')), '--browser', str(tmp_path)],
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((tmp_path/'ui-result.json').read_text(encoding='utf-8'))
    assert record['clockBefore'] != record['clockAfter']
    if '23:59:59' in clock:
        assert record['clockBefore'].startswith('2026-10-03T14:59:59')
        assert record['clockAfter'].startswith('2026-10-03T15:00:01')
