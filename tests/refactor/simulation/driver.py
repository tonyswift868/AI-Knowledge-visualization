"""Visible-state controller. No direct plan creation, source seeding or grading."""
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import time

from .engine import StopRun

STAGES=('form','dialogue','sources','map','estimate','generated','classroom','final','report','free')


def controls(view,stage):
    def a(method,params=''):return dict(method=method,params=params)
    if stage=='dialogue':return [a('plan.say','text: 回答当前追问、认可预览或请求修改')]
    if stage=='sources':
        decisions=[{'card_id':c['card_id'],'adopt':c.get('status')!='adopted'} for c in view.get('sources',[])]
        result=[a('plan.decide','params 是单张卡片的对象 {card_id, adopt}，不是数组；可选组合：'+json.dumps(decisions,ensure_ascii=False)),
                a('plan.say','text: 资料选择/范围要求'),a('plan.more','再找找')]
        if any(c.get('status')=='adopted' for c in view.get('sources',[])):
            result.append(a('plan.outline','资料已认可，愿意按现有资料出地图时点击；不需要再次采用已采用卡片'))
        return result
    if stage=='map':return [a('plan.say','text: 修改或认可知识地图'),a('plan.estimate','认可后时间/花费预估')]
    if stage=='estimate':return [a('plan.generate','明确同意生成'),a('plan.say','text: 调整要求')]
    if stage=='generated':return [a('plan.start','开始计划')]
    if stage in ('classroom','final','free'):
        step=view.get('step');q=view.get('question');result=[]
        if q:return [a('classroom.answer','value: choice/multi下标数组; fill每空文本数组; 其他文本'),
            a('classroom.reply','text: 作答或澄清'),a('classroom.ask','text: 追问'),a('simulation.reopen','中途退出再进')]
        if step=='preparing_materials':return [a('classroom.state','等待后台准备')]
        result=[a('classroom.ask','text: 随时提问'),a('simulation.reopen','退出再进')]
        if (view.get('conversation') or {}).get('interrupted'):result.append(a('classroom.back','回原来的位置'))
        if step in ('teach','teaching','closing'):result.append(a('classroom.next','懂了继续'))
        if step in ('opening','teaching','closing'):result.extend([a('classroom.confused','没太懂'),a('classroom.reply','text: 开场回答或我会了')])
        if step=='opening':result.append(a('classroom.choose','option: 开场选项下标'))
        if view.get('can_skip'):result.append(a('classroom.test','kind: skip_test'))
        if step=='test_offer':result.append(a('classroom.test','kind: module_test'))
        if step=='test_done':
            if view.get('red'):result.append(a('classroom.test','kind: retest'))
            else:result.append(a('classroom.next_module','下模块'))
        if step in ('check_offer','checking') and not q:result.append(a('classroom.check','开始或继续最终检验'))
        return result
    return []


class Driver:
    def __init__(self,simulation,user,persona,*,stuck_limit=5,reopen_before_adopt=False):
        self.sim,self.user,self.persona=simulation,user,persona
        self.stuck_limit=stuck_limit
        self.reopen_before_adopt=reopen_before_adopt
        if stuck_limit<1:raise ValueError('stuck_limit must be positive')
        p=simulation.evidence.root/'checkpoint.json'
        self.state=json.loads(p.read_text(encoding='utf-8')) if p.exists() else dict(stage='form',plan_id=None,rounds=0,view={},flags=[])
        self.previous=None;self.repeats=0
        if hasattr(user,'flags'):user.flags=set(self.state.get('user_flags',[]))
        if hasattr(user,'script') and 'user_script_remaining' in self.state:user.script=list(self.state['user_script_remaining'])
        if 'clock' in self.state:simulation.clock.value=simulation.clock.value.fromisoformat(self.state['clock'])
    def save(self):
        self.state['clock']=self.sim.clock.now().isoformat()
        if hasattr(self.user,'flags'):self.state['user_flags']=sorted(self.user.flags)
        if hasattr(self.user,'script'):self.state['user_script_remaining']=list(self.user.script)
        self.sim.evidence.save('checkpoint.json',self.state)
    def request(self,method,**params):
        if method.startswith(('plan.','classroom.')) and method not in ('plan.current','plan.drafts'):
            params.setdefault('plan_id',self.state['plan_id'])
        result=self.sim.call(method,**params)
        view=result.get('draft',result);self.state['view']=view
        if view.get('plan_id'):self.state['plan_id']=view['plan_id']
        self.save();return view
    @staticmethod
    def fingerprint(view,stage):
        # Growing chat IDs/streams/busy labels are not evidence of progress.
        value=dict(stage=stage,phase=view.get('phase'),step=view.get('step'),module=view.get('module'),
            question=(view.get('question') or {}).get('id'),number=(view.get('question') or {}).get('number'),
            point_states=view.get('points'),red=view.get('red'),check=view.get('check'),
            conversation=view.get('conversation'),preparation=view.get('preparation'))
        if stage in ('dialogue','sources','map','estimate'):
            value.update(latest_ai=next((m.get('text') for m in reversed(view.get('messages',[])) if m.get('role')=='ai'),None),
                sources=[(c.get('card_id'),c.get('status')) for c in view.get('sources',[])],outline=view.get('outline'))
        return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    def tick(self):
        key=self.fingerprint(self.state['view'],self.state['stage'])
        self.repeats=self.repeats+1 if key==self.previous else 0;self.previous=key
        if self.repeats>=self.stuck_limit:
            raise StopRun('STUCK',f'同一步无进展{self.stuck_limit}次；未自动跳过。')
    def action(self):
        view=self.state['view'];stage=self.state['stage'];actions=controls(view,stage)
        picked=self.user.choose(view,actions,stage)
        if picked.get('method') not in {a['method'] for a in actions} or not isinstance(picked.get('params'),dict):
            raise StopRun('USER_ACTION_INVALID',str(picked))
        self.sim.evidence.event('simulated_user',stage=stage,action=picked,simulated_now=self.sim.clock.now().isoformat())
        if picked['method']=='simulation.reopen':
            self.state['view']=self.sim.reopen(self.state['plan_id'])
            if hasattr(self.user,'gateway'):self.user.gateway=self.sim.gateway
            self.state['flags'].append('reopen');self.save()
        else:
            if picked['method']=='classroom.state':time.sleep(.1)
            self.request(picked['method'],**picked['params'])
        if stage=='dialogue':self.state['rounds']+=1;self.save()
    def planning(self,to,rounds):
        if self.state['stage']=='form':
            self.request('plan.current')
            if to=='form':return
            today=self.sim.clock.day();p=self.persona
            form=dict(goal=p.goal,final_goal=p.final_goal,level=p.level,level_note=p.level_note,notes=p.notes,
                start=today.isoformat(),end=(today+timedelta(days=p.days-1)).isoformat(),daily_cap=p.daily_cap,
                weekday_weights=[1]*7,special_days=[],only_mine=False)
            self.request('plan.submit',form=form);self.state.update(stage='dialogue',rounds=1);self.save()
        while STAGES.index(self.state['stage'])<STAGES.index('classroom'):
            stage=self.state['stage'];view=self.state['view'];phase=view.get('phase')
            if stage=='dialogue':
                if rounds and self.state['rounds']>=rounds:return
                # Stop this segment at the actual preview, before consent/search.
                if phase=='preview' and to=='dialogue':return
                if phase not in ('questions','preview','clarification'):
                    if to=='dialogue':return
                    self.state['stage']='sources';self.save();continue
            if stage=='sources' and phase=='outline':self.state['stage']='map';self.save();continue
            if (stage=='sources' and self.reopen_before_adopt and view.get('sources')
                    and 'draft_reopen' not in self.state['flags']):
                self.state['view']=self.sim.reopen_draft(self.state['plan_id'])
                if hasattr(self.user,'gateway'):self.user.gateway=self.sim.gateway
                self.state['flags'].append('draft_reopen');self.save();continue
            if stage=='map' and view.get('estimate'):self.state['stage']='estimate';self.save();continue
            if stage=='estimate' and phase=='ready':
                self.sim.join_jobs();self.state['stage']='generated';self.save();continue
            if stage=='generated' and view.get('ok'):
                self.state['stage']='classroom';self.request('classroom.enter');continue
            if to==stage and stage!='dialogue':return
            self.tick();self.action()
            if stage=='generated':
                # Start response has no draft. Read the actual classroom once.
                self.state['stage']='classroom';self.request('classroom.enter');return
    def new_day(self):
        self.request('classroom.leave');self.sim.advance_day();self.request('classroom.enter')
        self.state['flags'].append('next_day');self.save()
    def classroom(self,to,day_limit=None):
        if not self.state['view'].get('step'):self.request('classroom.enter')
        while True:
            view=self.state['view'];stage=self.state['stage'];step=view.get('step')
            if stage=='classroom' and step=='check_offer':
                if to=='classroom':return
                self.state['stage']='final';self.save();continue
            if stage=='final' and step=='completed':
                if to=='final':return
                self.state['stage']='report';self.save();return
            if stage=='free' and step=='completed' and self.state.get('practice_started'):return
            today=view.get('today') or {}
            if stage=='classroom' and step=='teach' and not view.get('question') and today.get('today_target',0)>0 and (
                    today.get('today_confirmed',0)+today.get('today_tentative',0)>=today['today_target']):
                if day_limit:return
                self.new_day();continue
            # Segment replay may include module end; advance explicitly before
            # the next module so offline coverage includes several study dates.
            if stage=='classroom' and step=='test_done' and not view.get('red') and 'day-'+str(view['module']['number']) not in self.state['flags']:
                if day_limit:return
                self.state['flags'].append('day-'+str(view['module']['number']));self.new_day();continue
            self.tick();self.action()
    def run(self,*,to='free',from_stage=None,rounds=None,day=None):
        try:
            if to not in STAGES:raise ValueError('unknown segment')
            if from_stage and self.state['stage']!=from_stage:
                raise StopRun('CHECKPOINT_STAGE',f'检查点在{self.state["stage"]}，不能跳到{from_stage}或塞入资料。')
            if day is not None:
                if self.state['stage']=='generated':
                    self.request('plan.start');self.state['stage']='classroom';self.request('classroom.enter');self.save()
                if self.state['stage'] not in ('classroom','final'):raise StopRun('CHECKPOINT_REQUIRED','只跑某天需已建好的课堂检查点。')
                if day<1:raise ValueError('day must be >= 1')
                plan=self.sim.services.store.get_plan(self.state['plan_id']);target=self.sim.clock.value.fromisoformat(plan['window']['start']+'T10:00:00'+self.sim.clock.now().strftime('%z'))
                target+=timedelta(days=day-1)
                if target<self.sim.clock.now():raise StopRun('CLOCK_REWIND','不回拨已学日期；请另用隔离运行目录。')
                self.request('classroom.leave');self.sim.clock.value=target;self.request('classroom.enter');self.save()
            if STAGES.index(self.state['stage'])<STAGES.index('classroom'):self.planning(to,rounds)
            if self.state['stage'] in ('classroom','final') and STAGES.index(to)>=STAGES.index(self.state['stage']):self.classroom(to,day)
            if self.state['stage']=='report' and STAGES.index(to)>=STAGES.index('report'):
                result=self.sim.call('plan.report',plan_id=self.state['plan_id']);self.sim.evidence.save('learning-report.json',result)
                self.sim.call('plan.report_export',plan_id=self.state['plan_id']);self.sim.call('plan.report_pdf',plan_id=self.state['plan_id'])
                if to=='report':return self.finish()
                self.state['stage']='free';self.request('classroom.practice',count=3);self.state['practice_started']=True;self.save()
                self.classroom(to)
            elif self.state['stage']=='free' and to=='free':
                if self.state['view'].get('step')=='completed':self.request('classroom.practice',count=3)
                self.state['practice_started']=True;self.save();self.classroom(to)
            return self.finish()
        except Exception as exc:
            self.sim.gateway.halt();self.sim.evidence.stopped=dict(code=getattr(exc,'code',type(exc).__name__),message=str(exc))
            self.sim.evidence.issue(getattr(exc,'code',type(exc).__name__),str(exc),'checkpoint.json')
            return self.finish()
    def finish(self):
        try:self.sim.join_jobs()
        except Exception as exc:
            self.sim.gateway.halt();self.sim.evidence.stopped=dict(code=getattr(exc,'code',type(exc).__name__),message=str(exc))
            self.sim.evidence.issue(getattr(exc,'code',type(exc).__name__),str(exc),'events.jsonl')
        if self.sim.gateway.halted.is_set() and not self.sim.evidence.stopped:
            self.sim.evidence.stopped=dict(code='MODEL_STOPPED',message='后台模型调用已止损，详见调用原始证据。')
        self.save();return self.sim.evidence.summary(self.state['stage'],self.sim.gateway.spent)
