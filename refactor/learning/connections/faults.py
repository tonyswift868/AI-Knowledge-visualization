"""Program-owned model fault presentation; never expose vendor exception text."""
import uuid
from .providers.registry import REGISTRY


def present_error(gateway, error, *, purpose='model', plan_id=''):
    """Also works with offline component gateways, without importing app state."""
    reporter = getattr(gateway, 'report_error', None)
    if callable(reporter):
        return reporter(error, purpose=purpose, plan_id=plan_id)
    notice = model_fault(error)
    if notice:
        notice.update(id=getattr(error,'request_id',None) or uuid.uuid4().hex,purpose=purpose,plan_id=plan_id)
        error.message = notice['message']
        error.model_fault = notice
    return notice


def model_fault(error, provider_id='', *, repeated=False):
    code, status = getattr(error, 'code', ''), getattr(error, 'status', None)
    provider = REGISTRY.get(provider_id)
    name = provider.display_name if provider else '模型'
    if code == 'MODEL_CANCELLED':
        return None
    reason = getattr(error, 'diagnostics', {}).get('reason', '')
    length = getattr(error, 'generated_text_length', 0)
    if code == 'MODEL_INSUFFICIENT_BALANCE' or status == 402:
        category, message, short = 'balance', f'{name} 余额不足，充值后点重试', '模型余额不足'
    elif code in {'MODEL_AUTH_FAILED', 'MODEL_PERMISSION_DENIED', 'MODEL_KEY_REQUIRED', 'MODEL_NOT_CONFIGURED'} or status in {401,403}:
        category, message, short = 'key', 'API Key 无效，请到设置里检查', '模型 Key 无效'
        if code in {'MODEL_KEY_REQUIRED', 'MODEL_NOT_CONFIGURED'}:
            message = getattr(error, 'message', '') or message
    elif code == 'MODEL_RATE_LIMITED' or status == 429:
        category, message, short = 'rate', '请求太频繁，请稍等再试', '模型请求频繁'
    elif code == 'MODEL_CONTEXT_WINDOW_EXCEEDED':
        category, message, short = 'input', '这次给模型的内容太长，请减少所选资料后再试。', '模型输入过长'
    elif (code == 'MODEL_FIRST_EVENT_TIMEOUT' or status in {500,503}
          or code == 'MODEL_TIMEOUT' and getattr(error,'response_received',False) and not length):
        category, message, short = 'busy', f'模型暂时无法使用（{name} 服务繁忙），请稍后再试', '模型暂不可用'
    elif (length and (code in {'MODEL_CONNECTION_FAILED','MODEL_TIMEOUT'} or
                      reason in {'incomplete_chat_message','incomplete_anthropic_message','protocol_validation:unterminated_sse_frame'})):
        category, message, short = 'interrupted', '回复中断了，可以重试', '模型回复中断'
    elif code == 'MODEL_TIMEOUT' and reason == 'connect_timeout':
        category, message, short = 'network', '连接模型服务太慢，请稍后重试', '模型连接超时'
    elif code in {'MODEL_CONNECTION_FAILED','MODEL_TIMEOUT'}:
        category, message, short = 'network', '连不上模型服务，请检查网络', '模型连接失败'
    elif code == 'AGENT_FORMAT':
        # Callers assign a safe, purpose-specific message after exhausting the
        # agent's format repair. Preserve it instead of presenting a software
        # fault as if it were a transport/vendor failure or user misunderstanding.
        category = 'format'
        message = getattr(error, 'message', '').strip() or 'AI 返回内容格式不正确，请重试。'
        short = 'AI 回复格式不正确'
    else:
        category, message, short = 'software', ('还是不行，请把这个情况告诉开发者。' if repeated else
            '软件出了点问题，没拿到回复（不是网络，也不是模型服务的问题），已自动记录。请点重试'), '模型回复异常'
    actions = [{'id':'settings','label':'去设置'}] if category == 'key' else [{'id':'retry','label':'重试'}]
    # Public provider pages only; endpoints/preset labels cannot supply arbitrary URLs.
    recharge = {'deepseek':'https://platform.deepseek.com/top_up', 'openai':'https://platform.openai.com/settings/organization/billing/overview',
                'anthropic':'https://platform.claude.com/settings/billing', 'qwen':'https://bailian.console.aliyun.com/',
                'kimi':'https://platform.moonshot.cn/console/account', 'zhipu':'https://open.bigmodel.cn/finance-center/recharge',
                'gemini':'https://aistudio.google.com/'}
    if category == 'balance':
        actions.insert(0, dict(id='recharge',label='去充值',**({'url':recharge[provider_id]} if provider_id in recharge else {})))
    return dict(category=category, code=code, message=message, short=short, actions=actions,
                provider_id=provider_id, provider_name=name)
