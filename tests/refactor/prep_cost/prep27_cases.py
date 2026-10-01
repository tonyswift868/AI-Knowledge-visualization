"""Fixed, human-authored source-grounded review exam; no model-written grading."""
from copy import deepcopy
import json
from pathlib import Path
import random


ARCHIVE = Path('D:/MentorFlow/.local/test-tmp/prep-retest')


def question(stem, answer, distractors, reason):
    return dict(type='choice', stem=stem, options=[answer, *distractors], answer=answer, required_points=[reason])


def good_lessons(samples):
    specifications = [
        ('丝路是相连路线网络，不是一条直线；整体直线距离约7500公里，沿具体路线超过35000公里。',
         '长安—天山廊道只是整体网络的一段，不能把廊道的年代或长度套给整体。',
         '有些路线已使用数千年；公元前2世纪交换规模显著增加，并非所有路线那时才出现。',
         '丝路是哪一种结构？','相连路线网络',['单条直线','只有一条海路'],'原文明确是相连网络。',
         '整体沿具体路线的长度如何？','超过35000公里',['恰好5000公里','恰好8700公里','不足100公里'],'原文是整体网络超过35000公里。'),
        ('长安—天山廊道跨度约5000公里，内部路线网络约8700公里，两个数字描述不同层次。',
         '它从长安/洛阳连到中亚七河地区，沿途经河西走廊、玉门关、天山南北两侧。',
         '廊道形成于公元前2世纪至公元1世纪，6至14世纪繁荣，作为主要贸易路线沿用至16世纪。',
         '约5000公里描述什么？','廊道跨度',['整体丝路全部长度','所有支线累计长度'],'跨度和内部路线网不是同一指标。',
         '廊道繁荣时期是？','公元6至14世纪',['公元前6至14世纪','公元20世纪','公元1世纪以前'],'原文给出的繁荣期为6至14世纪。'),
        ('append(x)在原列表末尾追加一个元素x，列表本身被修改。',
         '[引申1]独立例子：a=[1,2]，执行a.append(3)后a为[1,2,3]；把列表作为参数时，该列表整体成为一个元素。',
         'append修改原列表，正常返回None；不要把返回值当成修改后的列表。',
         'append修改哪里？','原列表末尾',['列表开头','新建副本末尾'],'追加到原列表末尾。',
         'a=[1,2]，执行r=a.append(3)，r是什么？','None',['[1,2,3]','3','报错'],'原地修改方法正常返回None。'),
        ('extend(iterable)把可迭代对象的元素逐项追加到原列表末尾。',
         '[引申1]每种方案各从a=[1,2]独立执行：append([3,4])得到[1,2,[3,4]]；extend([3,4])得到[1,2,3,4]。',
         'extend的效果类似a[len(a):]=iterable；它修改原列表而不是创建新的结果列表。',
         'extend追加什么？','参数中的各个元素',['参数整体作为一个元素','只追加第一个元素'],'extend逐项扩展列表。',
         '每种方案各从a=[1,2]独立执行，哪项正确？','append([3,4])得到[1,2,[3,4]]，extend([3,4])得到[1,2,3,4]',
         ['两者都得到[1,2,[3,4]]','两者都得到[1,2,3,4]','两者都不修改a'],'append整体追加，extend逐项追加。'),
        ('有理数能写成p/q，其中p、q为整数且q不为0。整数和分数都是有理数。',
         '[引申1]新例子：0可写成0/1，-2可写成-2/1；分子可为0或负数，但分母不能为0。',
         '0既不是正有理数，也不是负有理数；这不妨碍它属于有理数。',
         '定义允许分母为0吗？','不允许',['允许','只在分子为0时允许'],'定义要求q非零。',
         '0属于哪一项？','有理数，但既非正也非负',['不是有理数','正有理数','负有理数'],'0可写成0/1，且既非正也非负。'),
        ('分子分母同乘一个非零整数得到等值有理数；同除保持整数形式时需选共同因数。',
         '[引申1]新例子：2/3同乘2得到4/6，数值不变；4/6分子分母同除2得到2/3。',
         '标准形式要求分母为正整数、分子分母除1外没有共同因数；这不要求分子为正数。',
         '同乘非零整数需要是原分子分母的公因数吗？','不需要',['需要','只能乘1'],'同乘与约分的条件不同。',
         '哪项是标准形式？','-2/3',['2/-3','4/6','2/0'],'分母为正且分子分母互质。'),
    ]
    lessons=[]
    for sample,spec in zip(samples,specifications):
        a,b,c,stem,ans,wrong,reason,stem2,ans2,wrong2,reason2=spec
        refs=sample['point']['chunk_ids']
        guide=[dict(text=ans,response=reason)] + [dict(text=w,response='不符合题意；'+reason) for w in wrong]
        lessons.append(dict(steps=[dict(text=a,chunk_ids=refs,question=stem,options=guide),
            dict(text=b,chunk_ids=refs,question=None,options=[]),dict(text=c,chunk_ids=refs,question=None,options=[])],
            questions=[question(stem,ans,[*wrong,'无法由原文判断'],reason),question(stem2,ans2,wrong2,reason2)],
            extrapolations=['正文的新数值操作例子为引申，用于说明原文定义或方法。'] if '[引申1]' in b else []))
    return lessons


def exam(samples):
    lessons=good_lessons(samples)
    rows=[]
    def add(category,index,change,evidence):
        draft=deepcopy(lessons[index]);change(draft)
        rows.append(dict(category=category,context=deepcopy(samples[index]),draft=draft,evidence=evidence))
    add('fact',2,lambda d:d['steps'][0].update(text='append不会修改原列表，而是返回一个新列表。'),'官方append定义为末尾添加元素，原地修改。')
    add('fact',4,lambda d:d['steps'][0].update(text='有理数p/q的分母可以为0，包括0/0。'),'NCERT定义q非零。')
    add('number',1,lambda d:d['steps'][0].update(text='长安—天山廊道跨度约50000公里，内部路线网络约8700公里。'),'UNESCO跨度5000，不是50000。')
    add('number',5,lambda d:d['steps'][1].update(text='[引申1]2/3分子分母同乘2得到4/9，数值保持不变。'),'本地2/3!=4/9；同乘2应4/6。')
    def ambiguous_history(d):
        d['steps'][0]['question']='有人说丝路是一条从长安直通罗马的大道，最不符合原文哪一点？'
        d['steps'][0]['options']=[dict(text='丝路是相连路线网络',response='对，原文是网络。'),dict(text='丝路只有一条路',response='错，原文有多条路。'),dict(text='丝路只连接中国',response='错，原文连接多区域。')]
    add('ambiguity',0,ambiguous_history,'26批历史混合否定问法原型，两错误陈述均可按题意选择。')
    add('ambiguity',2,lambda d:d['steps'][0].update(question='下列哪一种说法不是不符合append的行为？'), '双重否定题意不清；回应只评价独立陈述。')
    add('premise',3,lambda d:d['questions'][1].update(stem='a=[1,2]，append([3,4])与extend([3,4])分别得到什么？'),'26批extend漏独立初值；连续执行有不同结果。')
    add('premise',2,lambda d:d['questions'][1].update(stem='调用append以后列表有几个元素？',answer=d['questions'][1]['options'][1]),'没有初始长度与参数，不能唯一确定。')
    for index in (2,3):
        def missing_example_label(d):
            d['steps'][1]['text']=d['steps'][1]['text'].replace('[引申1]','')
            d['steps'][0]['text']+='[引申1]类比排队末尾加人，仅说明末尾增加，不描述人的其他行为。'
            d['extrapolations']=['排队类比为引申，仅说明末尾增加。']
        add('extension',index,missing_example_label,'26批append/extend仅类比标引申，新数值例子未标；两原问题分别纳入，机械编号检查通过。')
    add('response',2,lambda d:d['steps'][0]['options'][0].update(response='错，append只在开头追加，末尾不生效。'),'正确选项被判错；与原文末尾添加相反。')
    add('response',4,lambda d:d['steps'][0]['options'][0].update(response='错，0/0合法，所以分母可以是0。'),'正确选项被判错；q必须非零。')
    for sample,draft in zip(samples,lessons):rows.append(dict(category='clean',context=deepcopy(sample),draft=draft,evidence='本人按指定官方原文及实际Python/Fraction结果核对为无错。'))
    random.Random(27).shuffle(rows)
    for i,row in enumerate(rows):row['id']=f'exam-{i:02d}'
    return rows


def archived_samples():
    return json.loads((ARCHIVE/'samples.json').read_text(encoding='utf-8'))
