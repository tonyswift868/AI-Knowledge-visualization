"""Scripted users/offline agents and an independent, screen-only model user."""
from dataclasses import dataclass, asdict, replace
import json
import random
from types import SimpleNamespace


@dataclass
class Persona:
    goal: str = '队列与栈'
    final_goal: str = '区分处理顺序'
    level: str = 'beginner'
    level_note: str = ''
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
        if stage == 'dialogue':
            if view.get('phase') == 'preview':
                return action('plan.say', text='确认并找资料')
            return action('plan.say', text='我想看懂队列和栈的处理顺序。请先说说学习路线。')
        if stage == 'sources' and view.get('phase')=='material_preview':
            return action('plan.say', text='确认结构')
        if stage == 'sources' and not view.get('sources'):
            return action('plan.say', text='https://docs.python.org/3/tutorial/datastructures.html')
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
            # Public option labels of the documented synthetic fixture only;
            # real quick choices are sorted by the material layer, not index 0.
            word = '队列' if purpose == 'quick' else '先进先出'
            correct = question['options'].index(word)
            return action('classroom.answer', value=[(correct+1)%len(question['options']) if wrong else correct])
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
资料卡片 status=adopted 表示已勾选并保存，不需要重复采用；plan.decide 的参数只能从当前给出的组合中选择。推荐链接不是已导入资料。操作员会下载上传或提供网址，不能自行声称已上传；有已导入资料时可选 plan.outline，先看按资料调整的结构，确认后才填知识点。material_preview明确确认用plan.say。
仅返回JSON {"method":"...","params":{...}}。choice/multi答案为从0开始的整数数组，fill为每空文本数组，其余文本。
这是一次模拟用户动作，不是产品意图理解请求。'''
        screen=view
        if stage in ('dialogue','sources','map','estimate','generated'):
            # NewPlan displays messages/cards/map, not the internal research
            # pool and quality audit. Those can grow by hundreds of KB and
            # were unnecessarily exhausting live simulation budgets.
            screen={k:v for k,v in view.items() if k in {'version','plan_id','phase','form','goal','focus',
                'messages','sources','outline','estimate','preview','recommendations','material_structure','busy','generation','name'}}
            if 'sources' in screen:
                screen['sources']=[{k:v for k,v in card.items() if k in {'card_id','source_id','status',
                    'title','url','summary','quality','tier','warnings','kind','site','chars','credibility','relevance','note'}} for card in view['sources']]
                for card in screen['sources']:
                    if isinstance(card.get('quality'),dict):
                        card['quality']={k:v for k,v in card['quality'].items() if k in {'credibility','relevance',
                            'eligible','note','source_group','publisher','source_type','community','level_fit','freshness','published_at'}}
        messages=[{'role':'user','content':json.dumps(dict(persona=asdict(self.persona), behavior=behavior,
            stage=stage, screen=screen, allowed_actions=actions),ensure_ascii=False)}]
        for attempt in range(2):
            # Only an invalid user action gets one explicit correction. Never
            # retry transport/model failures or a product bridge error here.
            result=self.gateway.complete('simulation_user',messages,system=system,max_tokens=4096)
            text=result.text.strip()
            if text.startswith('```'):text=text.split('\n',1)[1].rsplit('```',1)[0].strip()
            try:
                action=json.loads(text)
                if not isinstance(action,dict) or set(action)!={'method','params'} or not isinstance(action['params'],dict):
                    raise ValueError('模拟用户动作格式错误')
                if action['method'] not in {a['method'] for a in actions}:
                    raise ValueError('模拟用户选择了屏幕不存在的动作')
                if action['method']=='plan.decide':
                    offered=[{'card_id':c['card_id'],'adopt':c.get('status')!='adopted'} for c in view.get('sources',[])]
                    if action['params'] not in offered:
                        raise ValueError('模拟用户选择了屏幕不存在的资料勾选动作；已采用卡片不可再采用，只能取消')
                return action
            except ValueError as exc:
                evidence=getattr(self.gateway,'evidence',None)
                if evidence:evidence.event('simulated_user_action_rejected',stage=stage,text=text,reason=str(exc),attempt=attempt+1)
                if attempt:raise
                messages += [{'role':'assistant','content':result.text},{'role':'user','content':
                    '上一次动作没有执行。原因：'+str(exc)+'。屏幕没有变化，请重新根据当前屏幕和 allowed_actions 自己选择有效动作；不要声称操作已成功。'}]


class CompleteScenarioUser(ModelUser):
    """Public controls ensure required coverage; answers remain model-generated.

    No hidden bank/answer access. Persist flags via the existing checkpoint.
    """
    def __init__(self, gateway, persona):
        super().__init__(gateway, persona)
        self.flags = set()

    def choose(self, view, actions, stage):
        if stage not in ('classroom', 'final', 'free'):
            return super().choose(view, actions, stage)
        def action(method, **params): return dict(method=method, params=params)
        names = {a['method'] for a in actions}
        question = view.get('question')
        if question:
            purpose = question.get('purpose')
            forced = purpose in ('quick', 'module') and purpose not in self.flags
            old = self.persona
            self.persona = replace(old, wrong_ratio=1 if forced else 0,
                notes=old.notes + (' 本次有意答错，不看隐藏答案。' if forced else ' 本次认真按所学回答。'))
            try: result = super().choose(view, actions, stage)
            finally: self.persona = old
            if result['method'] in ('classroom.answer', 'classroom.reply') and forced:
                self.flags.add(purpose)
            return result
        if (view.get('conversation') or {}).get('interrupted') and 'classroom.back' in names:
            return action('classroom.back')
        step = view.get('step')
        if step == 'teaching':
            for flag, method, params in (
                ('confused', 'classroom.confused', {}),
                ('ask', 'classroom.ask', {'text': '刚才这个概念能再用一个生活里的例子解释吗？'}),
                ('reopen', 'simulation.reopen', {}),
            ):
                if flag not in self.flags and method in names:
                    self.flags.add(flag)
                    return action(method, **params)
        if step == 'opening': return action('classroom.choose', option=0)
        if step == 'test_offer': return action('classroom.test', kind='module_test')
        if step == 'test_done':
            return action('classroom.test', kind='retest') if view.get('red') else action('classroom.next_module')
        if step in ('check_offer', 'checking'): return action('classroom.check')
        if step == 'preparing_materials': return action('classroom.state')
        if 'classroom.next' in names: return action('classroom.next')
        return super().choose(view, actions, stage)


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
            from refactor.learning.course.planner_agents import PREVIEW_SYSTEM
            if kwargs.get('system') == PREVIEW_SYSTEM:
                return self.agent.complete(purpose,messages,**kwargs)
            self.clarifications+=1
            if self.clarifications<self.clarification_rounds:
                prompts=['想区分哪些顺序？','学完希望能怎么用？','以前看过哪些资料？','更喜欢读原理还是看例子？',
                    '想如何检验学习结果？','哪一天需要少学一点？','是否需要中文资料？']
                return SimpleNamespace(text=json.dumps(dict(enough=False,goal='队列与栈',focus=[],
                    questions=[prompts[(self.clarifications-1)%len(prompts)]]),ensure_ascii=False),search_hits=(),citations=())
        if purpose=='plan_intent':
            packet=json.loads(messages[-1]['content'].split('\n',1)[1]); phase=packet['ctx']['phase']
            if phase=='preview':
                intent={'kind':'supplement','instruction':packet['text']}
            else:
                intent={'kind':'answer'} if phase=='questions' else {'kind':'proceed','step':'outline'}
            return SimpleNamespace(text=json.dumps(dict(intents=[intent],reply='')),search_hits=(),citations=())
        return self.agent.complete(purpose,messages,**kwargs)
    def close(self): pass


def offline_fetch(url,**kwargs):
    from tests.refactor.integration38.test_offline_integrated38_flow import fetch
    return fetch(url,**kwargs)
