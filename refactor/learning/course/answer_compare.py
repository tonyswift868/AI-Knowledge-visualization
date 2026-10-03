"""Deterministic objective grading. No network, model, or student code execution."""
from decimal import Decimal, InvalidOperation, localcontext
import re
import unicodedata


def normalize(value):
    text=unicodedata.normalize('NFKC',value).casefold().replace('−','-')
    # Signs and decimal points carry meaning; -2 != 2 and 1.5 != 15.
    return ''.join(c for i,c in enumerate(text) if not c.isspace() and
        (not unicodedata.category(c).startswith('P') or c in '+-*/=^' or
         c=='.' and i>0 and i+1<len(text) and text[i-1].isdigit() and text[i+1].isdigit()))


_NUM=r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?'
_UNITS={}
for _dim,_scale,_aliases in (
    ('force','1','n|牛|牛顿'),('force','.001','mn|毫牛'),('force','1000','kn|千牛|千牛顿'),
    ('pressure','1','pa|帕|帕斯卡'),('pressure','1000','kpa|千帕'),('pressure','1000000','mpa|兆帕'),
    ('mass','1','kg|千克|公斤'),('mass','.001','g|克'),('mass','.000001','mg|毫克'),
    ('length','1','m|米'),('length','.01','cm|厘米'),('length','.001','mm|毫米'),('length','1000','km|千米|公里'),
    ('area','1','m2|m^2|平方米'),('area','.0001','cm2|cm^2|平方厘米'),
    ('volume','1','m3|m^3|立方米'),('volume','.001','l|升'),('volume','.000001','ml|毫升'),
    ('time','1','s|秒'),('time','60','min|分钟|分'),('time','3600','h|小时'),
    ('speed','1','m/s|米/秒'),('speed','0.2777777777777777777777777778','km/h|千米/小时'),
    ('voltage','1','v|伏|伏特'),('voltage','.001','mv|毫伏'),
    ('current','1','a|安|安培'),('current','.001','ma|毫安'),
    ('energy','1','j|焦|焦耳'),('energy','1000','kj|千焦'),
    ('power','1','w|瓦|瓦特'),('power','1000','kw|千瓦'),
    ('temperature','1','°c|摄氏度'),('ratio','.01','%|百分比'),
):
    for _alias in _aliases.split('|'):_UNITS[_alias]=(_dim,Decimal(_scale))
_UNITS.update({'mN':('force',Decimal('.001')),'MN':('force',Decimal('1000000')),
               'mPa':('pressure',Decimal('.001')),'MPa':('pressure',Decimal('1000000'))})


def _quantity(value):
    if not isinstance(value,str) or len(value)>4000:return None
    text=unicodedata.normalize('NFKC',value).strip().replace('−','-')
    text=re.sub(r'^(?:答案|结果|答)(?:是|为|:|：)?\s*','',text).rstrip('。;；').strip()
    text=re.sub(r'^[a-zA-Z]\s*=\s*','',text)
    text=re.sub(r'(?<=\d)\s*[×*]\s*10\s*\^\s*([+-]?\d+)',r'e\1',text)
    text=re.sub(r'(?<=\d),(?=\d{3}(?:\D|$))','',text)
    match=re.fullmatch(rf'({_NUM})(?:\s*/\s*({_NUM}))?\s*([^\d\s].*?)?',text,re.I)
    if not match:return None
    try:
        with localcontext() as context:
            context.prec=40
            number=Decimal(match[1])
            if match[2]:number/=Decimal(match[2])
        if not number.is_finite():return None
    except (InvalidOperation,ZeroDivisionError):return None
    unit=''.join((match[3] or '').split())
    if unit not in ('mN','MN','mPa','MPa'):unit=unit.casefold()
    if unit and unit not in _UNITS:return None
    return number,unit


def _standard(question):
    answer=question.get('answer')
    if not isinstance(answer,str):return None
    # Worked legacy source answers: take final numeric RHS, never evaluate code.
    answer=answer.rsplit('=',1)[-1].strip()
    tolerance=question.get('tolerance',0)
    if '±' in answer:
        answer,error=answer.split('±',1)
        parsed=_quantity(error)
        if not parsed:return None
        tolerance,unit=parsed
        if unit:answer=answer.strip()+unit
    else:
        try:tolerance=Decimal(str(tolerance))
        except InvalidOperation:return None
    if not tolerance.is_finite() or tolerance<0:return None
    expected=_quantity(answer)
    return (expected,tolerance) if expected else None


def can_grade(question):
    kind=question.get('kind')
    if kind in ('choice','multi','fill'):return True  # host validates bank structure
    return kind=='calc' and _standard(question) is not None


def compare_number(question,reply):
    standard=_standard(question);actual=_quantity(reply)
    if standard is None or actual is None:return False
    (expected,unit),tolerance=standard
    value,actual_unit=actual
    if unit and actual_unit:
        dim,scale=_UNITS[unit];other_dim,other_scale=_UNITS[actual_unit]
        if dim!=other_dim:return False
        return abs(value*other_scale-expected*scale)<=tolerance*scale
    # Omitted unit adopts the standard unit. An unlabelled standard only
    # specifies a number; a recognized suffix does not change that number.
    return abs(value-expected)<=tolerance


def choice_text(question,text):
    """Literal option or explicit A/B labels, never semantic AI judgement."""
    exact=[i for i,option in enumerate(question['options']) if normalize(text)==normalize(option)]
    if len(exact)==1:return exact
    letters=unicodedata.normalize('NFKC',text).strip().upper()
    letters=re.sub(r'[\s,，、;；]+','',letters)
    if not re.fullmatch(r'[A-Z]+',letters):return None
    indices=[ord(c)-ord('A') for c in letters]
    if any(i>=len(question['options']) for i in indices) or len(set(indices))!=len(indices):return None
    if question['kind']=='choice' and len(indices)!=1:return None
    return indices


def grade(question,value):
    kind=question['kind']
    if kind in ('choice','multi'):
        if not isinstance(value,list) or not value or any(type(i) is not int or not 0<=i<len(question['options']) for i in value) or len(set(value))!=len(value) or kind=='choice' and len(value)!=1:
            raise ValueError('请选择有效的选项。')
        return set(value)==set(question['answer']),'、'.join(question['options'][i] for i in sorted(value))
    if kind=='fill':
        if not isinstance(value,list) or len(value)!=len(question['answer']) or any(not isinstance(v,str) or len(v)>4000 for v in value):
            raise ValueError('请填写每一个空。')
        good=all(any(normalize(v)==normalize(a) for a in accepted) for v,accepted in zip(value,question['answer']))
        return good,'；'.join(value)
    if kind=='calc':
        if not isinstance(value,str) or len(value)>4000:raise ValueError('请填写计算数值，可带单位。')
        return compare_number(question,value),value
    raise ValueError('这道旧主观题不计分。')
