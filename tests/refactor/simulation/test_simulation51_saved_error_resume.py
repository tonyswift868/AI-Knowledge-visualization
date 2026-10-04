"""A read-only draft reload must not mistake a retained error for a new one."""
from refactor.learning.app.plan_flow import PlanFlow
from tests.refactor.simulation.engine import Simulation,Clock
from tests.refactor.simulation.actors import OfflineGateway
from tests.refactor.simulation.actors import ScriptUser,Persona
from tests.refactor.simulation.driver import Driver


def test_simulation51_read_saved_error_allows_explicit_retry_without_erasing_it(tmp_path):
    sim=Simulation(tmp_path,Clock('2026-10-03T10:00:00+09:00'),lambda _:OfflineGateway(),offline=True)
    try:
        view=sim.call('plan.current')['draft']
        flow=PlanFlow(sim.services,lambda *a:None,sim.clock.day)
        draft=flow.load(view['plan_id'])
        old=dict(id='previous-failure',role='ai',kind='error',text='上一次输出被截断，请重试。')
        draft['messages'].append(old);flow.save(draft)
        result=sim.call('plan.get',plan_id=draft['plan_id'])['draft']
        assert result['messages'][-1]==old and not sim.gateway.halted.is_set()
        assert not sim.evidence.calls
    finally:sim.close()


def test_simulation51_rejected_operator_file_does_not_upload_again(tmp_path):
    sim=Simulation(tmp_path,Clock('2026-10-03T10:00:00+09:00'),lambda _:OfflineGateway(),offline=True)
    try:
        path=tmp_path/'material.md'
        path.write_text('# 教材\n\n'+('本合成教材由测试教育平台编写发布。队列按先进先出的顺序处理元素。\n\n'*30),encoding='utf-8')
        driver=Driver(sim,ScriptUser(),Persona(),material_file=path)
        driver.run(to='sources')
        # The operator upload was consumed exactly once. Reject through the
        # actual source decision, then continue and pause for a new selection.
        assert driver.state['stage']=='sources'
        source=driver.state['view']['sources'][0]
        driver.request('plan.decide',card_id=source['card_id'],adopt=False)
        driver.planning('map',None)
        assert sum(s['method']=='plan.upload' for s in sim.evidence.steps)==1
        assert sim.upload_path is None and driver.material_file is None
    finally:sim.close()
