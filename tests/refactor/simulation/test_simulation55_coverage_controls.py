"""Required scenario controls use public state; answers still use the model."""
import json
from types import SimpleNamespace
from .actors import CompleteScenarioUser, Persona
from .driver import controls


def test_simulation55_coverage_controls_and_screen_only_answers():
    class Model:
        def __init__(self):self.inputs=[]
        def complete(self,purpose,messages,**kwargs):
            self.inputs.append(json.loads(messages[0]['content']))
            return SimpleNamespace(text=json.dumps({'method':'classroom.answer','params':{'value':[1]}}))
    model=Model();user=CompleteScenarioUser(model,Persona())
    view={'step':'teaching','conversation':{}}
    assert [user.choose(view,controls(view,'classroom'),'classroom')['method'] for _ in range(3)]==[
        'classroom.confused','classroom.ask','simulation.reopen']
    assert user.choose(view,controls(view,'classroom'),'classroom')['method']=='classroom.next'
    interrupted={'step':'teaching','conversation':{'interrupted':True}}
    assert user.choose(interrupted,controls(interrupted,'classroom'),'classroom')['method']=='classroom.back'
    for purpose in ('quick','module','module'):
        view={'step':'question','question':{'purpose':purpose,'stem':'Public text','options':['a','b','c']}}
        user.choose(view,controls(view,'classroom'),'classroom')
    assert [r['behavior']['intend_wrong'] for r in model.inputs]==[True,True,False]
    assert all('answer' not in r['screen']['question'] for r in model.inputs)
    assert user.flags=={'confused','ask','reopen','quick','module'}
