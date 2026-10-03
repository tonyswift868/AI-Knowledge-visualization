"""Scripted users/offline agents and an independent, screen-only model user."""
from dataclasses import dataclass, asdict
import json
import random
from types import SimpleNamespace


@dataclass
class Persona:
    goal: str = '队列与栈'
    final_goal: str = '区分处理顺序'
    level: str = 'beginner'
    style: str = '简短口语，愿意提问'
    wrong_ratio: float = .2
    ask_ratio: float = .1
    known_ratio: float = .1
    exit_ratio: float = .03
    days: int = 7
    daily_cap: int = 60
    notes: str = ''
    seed: int = 42

    def __post_init__(self):
        for key in ('wrong_ratio', 'ask_ratio', 'known_ratio', 'exit_ratio'):
            if not 0 <= getattr(self, key) <= 1: raise ValueError(key+' must be 0..1')
        if self.days < 1: raise ValueError('days must be positive')


class ScriptUser:
    """Fixed offline replies. No store access, hidden answer or prebuilt plan."""
    def __init__(self, script=None):
        self.script = list(script or [])
        self.flags = set()

    def choose(self, view, actions, stage):
        if self.script:
            return self.script.pop(0)
        names = {a['method'] for a in actions}
        def action(method, **params): return dict(method=method, params=params)
        if stage == 'dialogue': return action('plan.say', text='我想看懂队列和栈的处理顺序。请先说说学习路线。')
        if stage == 'sources':
            cards = [c for c in view.get('sources', []) if c['status'] == 'new']
            if cards: return action('plan.decide', card_id=cards[0]['card_id'], adopt=True)
            return action('plan.outline')
        if stage == 'map': return action('plan.estimate')
        if stage == 'estimate': return action('plan.generate')
        if stage == 'generated': return action('plan.start')
        question = view.get('question')
        if question:
            purpose = question['purpose']
            wrong = purpose in ('quick', 'module') and purpose not in self.flags
            if wrong: self.flags.add(purpose)
            # Fixed answers for the documented synthetic choice fixture only.
            return action('classroom.answer', value=[1 if wrong else 0])
        if 'simulation.reopen' in names and 'exit' not in self.flags and view.get('step') == 'teaching':
            self.flags.add('exit'); return action('simulation.reopen')
        if 'classroom.ask' in names and 'ask' not in self.flags and view.get('step') == 'teaching':
            self.flags.add('ask'); return action('classroom.ask', text='我想问，为什么处理顺序不一样？')
        if (view.get('conversation') or {}).get('interrupted'):
            return action('classroom.back')
        if view.get('step') == 'teach' and view.get('can_skip') and view['module']['number'] == 2:
            return action('classroom.test', kind='skip_test')
        if view.get('step') == 'teaching' and view['module']['number'] == 3 and 'known' not in self.flags:
            self.flags.add('known'); return action('classroom.reply', text='我会了')
        if view.get('step') == 'opening': return action('classroom.choose', option=0)
        if view.get('step') == 'test_offer': return action('classroom.test', kind='module_test')
        if view.get('step') == 'test_done':
            return action('classroom.test', kind='retest') if view.get('red') else action('classroom.next_module')
        if view.get('step') in ('check_offer', 'checking'): return action('classroom.check')
        return action('classroom.next')


class ModelUser:
    """Only the public bridge view is sent; no bank answers or evaluator verdicts."""
    def __init__(self, gateway, persona):
        self.gateway, self.persona = gateway, persona
        self.rng = random.Random(persona.seed)

    def choose(self, view, actions, stage):
        question = view.get('question')
        # Sampling is a persona instruction, never a quality score of the host.
        behavior = dict(intend_wrong=bool(question) and self.rng.random()<self.persona.wrong_ratio,
            ask=self.rng.random()<self.persona.ask_ratio, claim_known=self.rng.random()<self.persona.known_ratio,
            leave=self.rng.random()<self.persona.exit_ratio)
        system = '''扮演真实学习者，按人设与本次屏幕实时决定下一步，不能读取隐藏答案或伪造已执行操作。
你不是教师、评测员或质量打分员。不要点评教学质量，不要求程序判你正确；有时答错、有时追问、有时说我会了、有时退出再进。
只能选择allowed_actions中列出的method，参数照其说明。不要预写后续台词或跳过聊天、资料选择。聊天时具体回答屏幕里的反问，暂不懂的直接说；看到路线后表达认可或具体修改。
仅返回JSON {"method":"...","params":{...}}。choice/multi答案为从0开始的整数数组，fill为每空文本数组，其余文本。
这是一次模拟用户动作，不是产品意图理解请求。'''
        result = self.gateway.complete('simulation_user', [{'role':'user','content':json.dumps(
            dict(persona=asdict(self.persona), behavior=behavior, stage=stage, screen=view, allowed_actions=actions),ensure_ascii=False)}],
            system=system, max_tokens=2048)
        text=result.text.strip()
        if text.startswith('```'): text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
        action=json.loads(text)
        if not isinstance(action,dict) or set(action)!={'method','params'} or not isinstance(action['params'],dict):
            raise ValueError('模拟用户动作格式错误')
        if action['method'] not in {a['method'] for a in actions}: raise ValueError('模拟用户选择了屏幕不存在的动作')
        return action


class OfflineGateway:
    """Uses existing synthetic agents/web, not fake bridge handlers or seed_bank."""
    def __init__(self,clarification_rounds=3):
        from tests.refactor.integration38.test_offline_integrated38_flow import IntegratedModel
        self.agent=IntegratedModel(); self.clarifications=0;self.clarification_rounds=clarification_rounds

    def get_configuration(self): return {'provider_id':'deepseek','model':'deepseek-v4-flash'}
    def estimate_context(self): return {'region':'global','service_tier':'standard'}
    @property
    def price_catalog(self):
        from refactor.learning.connections.pricing import PriceCatalog
        return PriceCatalog.empty()
    def complete(self,purpose,messages,**kwargs):
        if purpose=='sufficiency':
            self.clarifications+=1
            if self.clarifications<self.clarification_rounds:
                prompts=['想区分哪些顺序？','学完希望能怎么用？','以前看过哪些资料？','更喜欢读原理还是看例子？',
                    '想如何检验学习结果？','哪一天需要少学一点？','是否需要中文资料？']
                return SimpleNamespace(text=json.dumps(dict(enough=False,goal='队列与栈',focus=[],
                    questions=[prompts[(self.clarifications-1)%len(prompts)]]),ensure_ascii=False),search_hits=(),citations=())
        if purpose=='plan_intent':
            packet=json.loads(messages[-1]['content'].split('\n',1)[1]); phase=packet['ctx']['phase']
            intent={'kind':'answer'} if phase=='questions' else {'kind':'proceed','step':'outline'}
            return SimpleNamespace(text=json.dumps(dict(intents=[intent],reply='')),search_hits=(),citations=())
        return self.agent.complete(purpose,messages,**kwargs)
    def close(self): pass


def offline_fetch(url,**kwargs):
    from tests.refactor.integration38.test_offline_integrated38_flow import fetch
    return fetch(url,**kwargs)
