"""Operator-selected local upload, real import/store/materials, no web search."""
from pathlib import Path
import socket

from tests.refactor.simulation.actors import OfflineGateway, ScriptUser, Persona, offline_fetch
from tests.refactor.simulation.driver import Driver
from tests.refactor.simulation.engine import Simulation, Clock
from tests.refactor.integration38.test_offline_integrated38_flow import BODY


def test_simulation51_operator_upload_through_real_picker_to_free(tmp_path, monkeypatch):
    monkeypatch.setattr(socket, 'create_connection', lambda *a,**k: (_ for _ in ()).throw(AssertionError('offline network')))
    local_body=BODY.replace(b'<head>', b'<head><meta charset="utf-8">').replace(
        b'</body>', '<p>本合成教材由测试教育平台编写发布。</p></body>'.encode())
    selected = tmp_path/'selected-course.html'; selected.write_bytes(local_body)
    sim = Simulation(tmp_path, Clock(), lambda _:OfflineGateway(), fetcher=offline_fetch, offline=True, timeout=30)
    try:
        driver = Driver(sim, ScriptUser(), Persona(), material_file=selected, reopen_before_adopt=True)
        result = driver.run(to='free')
        assert result['stop'] is None, result
        assert result['stage']=='free' and result['cost_cny']=='0'
        assert 'draft_reopen' in driver.state['flags']
        assert any(s['method']=='plan.upload' for s in sim.evidence.steps)
        sources=sim.services.store.records('sources',driver.state['plan_id'])
        assert len(sources)==1 and sources[0]['kind']=='file'
        assert sim.services.store.get_original(sources[0]['sha256'])==local_body
        assert (tmp_path/'learning-report.pdf').read_bytes().startswith(b'%PDF-')
    finally:sim.close()
