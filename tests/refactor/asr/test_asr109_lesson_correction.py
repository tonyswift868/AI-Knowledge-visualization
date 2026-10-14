from types import SimpleNamespace
import threading
import time

from refactor.learning.voice.asr.lesson import vocabulary, protected_vocabulary, lesson_context
from refactor.learning.voice.asr.glossary import correct
from refactor.learning.voice.asr.correction import finish, needs_model, acceptable


def test_asr109_terms_are_current_lesson_bounded_and_deterministic():
    passages=['**向量检索**使用向量，向量记录含义。**Embedding**把文本变成向量。节点连接步骤，文档结构。']
    terms=vocabulary(['GraphRAG 与向量 RAG 的对比','Agent 工作流'], 'Embedding 是什么？',passages)
    assert {'向量','GraphRAG','Agent','Embedding'} <= set(terms)
    assert len(terms)<=160 and '那个' not in terms
    assert terms==vocabulary(['GraphRAG 与向量 RAG 的对比','Agent 工作流'], 'Embedding 是什么？',passages)
    protect=protected_vocabulary(passages)
    assert correct('项梁那个。',terms,protect_existing=False,protected_terms=protect)[0]=='向量那个。'
    assert correct('节点与文档结构。',['阶段','架构'],protected_terms=protect)[0]=='节点与文档结构。'
    assert correct('向量保存向梁。',['向量'],protect_existing=False)[0]=='向量保存向量。'
    assert correct('那个',['Agent'])[0]=='那个'
    assert correct('下料来个。',['向量'],lesson_match=True)[0]=='向量来个。'
    assert correct('graphra bag那边',['GraphRAG'],lesson_match=True)[0]=='GraphRAG那边'


def test_asr109_context_reads_only_current_module_and_latest_teacher():
    class Store:
        def records(self,kind,plan):
            assert plan=='p'
            return ({'points':[dict(id='x',module_id='m',title='向量检索',chunk_ids=['c']),
                               dict(id='other',module_id='else',title='牛顿',chunk_ids=['d'])],
                     'source_chunks':[dict(id='c',text='向量、向量、向量。'),dict(id='d',text='牛顿、牛顿。')]}[kind])
        def messages(self,plan,module):
            return [dict(role='assistant',content='旧问题'),dict(role='user',content='普通回答'),
                    dict(role='assistant',content='向量怎么查？')]
    result=lesson_context(Store(),'p','m','x')
    assert '向量' in result['terms'] and '牛顿' not in result['terms']
    assert result['question']=='向量怎么查？' and '向量' in result['protected']


def test_asr109_model_fixes_only_phonetic_text_and_never_answers_for_student():
    calls=[]
    class Gateway:
        def complete(self,purpose,messages,**kwargs):
            calls.append((purpose,messages,kwargs))
            return SimpleNamespace(text='向量那个。')
    assert needs_model('下料来个。',['向量'],'哪边擅长？')
    result=finish('下料来个。','下料来个。',['向量'],'哪边擅长？',Gateway())
    assert result.text=='向量那个。' and result.called
    assert calls[0][0]=='asr_correction' and calls[0][2]['max_tokens']==80
    assert not needs_model('那个',['Agent'],'什么是 Agent？')
    assert not acceptable('不知道','向量 RAG',['向量','RAG'])
    assert not acceptable('用节点连接步骤','Graph 用节点连接步骤',['Graph'])
    assert acceptable('graRAG那边','GraphRAG那边',['GraphRAG'])
    assert not acceptable('向量不适合','向量适合',['向量'])
    assert not acceptable('节点可以连接','阶段可以连接',['阶段'],protected_terms=['节点'])
    assert acceptable('香ra','向量RAG',['向量 RAG'])


def test_asr109_user_cancel_returns_local_and_cancels_late_model():
    stopped=threading.Event();cancel=threading.Event()
    class Slow:
        def complete(self,*args,**kwargs):
            cancel.set()
            kwargs['cancel'].wait(1)
            stopped.set()
            return SimpleNamespace(text='GraphRAG那边')
    started=time.monotonic()
    result=finish('graRAG那边','graRAG那边',['GraphRAG'],'哪边？',Slow(),cancel=cancel)
    assert result.text=='graRAG那边' and result.outcome=='cancelled'
    assert time.monotonic()-started<.3 and stopped.wait(.5)


def test_asr109_provider_acquisition_can_be_cancelled():
    release=threading.Event();cancel=threading.Event();calls=[]
    def provider():
        cancel.set();release.wait(1)
        return SimpleNamespace(complete=lambda *a,**k:calls.append(True))
    try:
        start=time.monotonic()
        result=finish('下料来个','下料来个',['向量'],'哪边？',provider,cancel=cancel)
        assert result.outcome=='cancelled' and time.monotonic()-start<.2
    finally: release.set()
    assert not calls


def test_asr109_user_long_transcript_preserves_uncertain_oral_fragments():
    raw=('graphra bag它是与于一些比较抽象的这个关系，它的它可以去建模，然后把它关起来，'
         '然后来找到而传统的这个香ra，它是。呃，对于一些比较明确的，所以他长得比较矮，'
         '所以咱都有自己的这个用制力。')
    terms=['GraphRAG','向量 RAG','向量','知识图谱','实体关系']
    assert needs_model(raw,terms,'赛季对阵这两件事是同一种查法还是各有各的拿手活？')
    candidate=raw.replace('graphra bag','GraphRAG')
    gateway=SimpleNamespace(complete=lambda *a,**k:SimpleNamespace(text=candidate))
    result=finish(raw,raw,terms,'赛季对阵这两件事是同一种查法？',gateway)
    assert result.text==candidate
    assert '长得比较矮' in result.text and '用制力' in result.text and '香ra' in result.text
