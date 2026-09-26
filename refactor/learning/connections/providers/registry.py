"""Official native-search contracts checked 2026-09-27; no key discovery.

Only DeepSeek has supplied live evidence. Defaults do not guarantee a model is
enabled for an individual account/region. URLs here never override caller URLs.
"""
from urllib.parse import urlsplit

from ..transport import normalize_url
from ..errors import ModelError
from .base import ProviderSpec, SearchSupport


CHECKED_ON = '2026-09-27'


def search(kind, url, notes, verified=False):
    return SearchSupport(kind, verified, url, CHECKED_ON, notes)


REGISTRY: dict[str, ProviderSpec] = {
    'deepseek': ProviderSpec(
        'deepseek', 'DeepSeek', 'anthropic_messages',
        'https://api.deepseek.com/anthropic', 'deepseek-v4-flash',
        search('anthropic_server_tool',
               'https://api-docs.deepseek.com/zh-cn/quick_start/agent_integrations/claude_code/',
               '已提供非流式实测样本：8 次搜索、44 条原始结果；流式未实测。'
               'input_tokens 已含缓存命中；encrypted_content 不是可读摘要。', True)),
    'qwen': ProviderSpec(
        'qwen', '通义千问', 'dashscope_generation',
        'https://dashscope.aliyuncs.com/api/v1', 'qwen-plus',
        search('dashscope_enable_search', 'https://help.aliyun.com/zh/model-studio/web-search',
               '未实测。用 DashScope 原生接口请求来源与引用；兼容 chat 接口不保证这些字段。'
               '不公开实际搜索词/精确次数；max_search_uses 无对应服务端参数。')),
    'kimi': ProviderSpec(
        'kimi', 'Kimi', 'openai_chat', 'https://api.moonshot.cn/v1', 'kimi-k3',
        search('builtin_function', 'https://platform.kimi.com/docs/guide/use-web-search',
               '未实测。$web_search 参数原样回传；内部完成工具往返并限制次数。'
               '官方未承诺搜索词、原始结果和引用字段；缺项保持空，不解析回答链接充数。'
               '工具参数 usage 已计入后续 prompt_tokens，不能重复相加。')),
    'zhipu': ProviderSpec(
        'zhipu', '智谱 GLM', 'openai_chat', 'https://open.bigmodel.cn/api/paas/v4', 'glm-5.3',
        search('chat_web_search',
               'https://docs.bigmodel.cn/api-reference/模型-api/对话补全',
               '未实测。tools.web_search.search_result 返回原始来源；'
               '实际搜索词/次数未报告，max_search_uses 无对应服务端参数。'
               '仅依据来源 refer 标记识别引用，不从普通链接推断引用。')),
    'openai': ProviderSpec(
        'openai', 'OpenAI', 'openai_responses', 'https://api.openai.com/v1', 'gpt-6-astra',
        search('responses_web_search', 'https://developers.openai.com/api/docs/guides/tools-web-search',
               '未实测。Responses web_search + include action.sources；以 max_tool_calls 限制工具。'
               'search_requests 计 search 动作，open_page/find_in_page 另保留在元数据。'
               '来源通常没有摘要，服务商未返回的底层检索结果不可恢复。')),
    'anthropic': ProviderSpec(
        'anthropic', 'Anthropic Claude', 'anthropic_messages',
        'https://api.anthropic.com', 'claude-opus-5-5',
        search('anthropic_server_tool',
               'https://platform.claude.com/docs/en/agents-and-tools/tool-use/web-search-tool',
               '未实测。使用不带动态过滤的 web_search_20250305；处理 pause_turn。'
               'input_tokens 不含 cache_read/cache_creation，需相加；加密内容不当摘要。')),
    'gemini': ProviderSpec(
        'gemini', 'Google Gemini', 'gemini_generate_content',
        'https://generativelanguage.googleapis.com/v1beta', 'gemini-3.8-flash',
        search('google_search_grounding',
               'https://ai.google.dev/gemini-api/docs/generate-content/google-search',
               '未实测。GenerateContent googleSearch；保留 groundingChunks/Supports。'
               'search_requests 是报告的查询数，不是金额/计费单元。'
               '工具输入与原始 totalTokenCount 对账，raw 标记不一致；'
               'max_search_uses 无对应服务端参数；展示时需遵守 searchEntryPoint 要求。')),
}


_HOSTS = {
    'api.deepseek.com': 'deepseek',
    'dashscope.aliyuncs.com': 'qwen', 'dashscope-intl.aliyuncs.com': 'qwen',
    'dashscope-us.aliyuncs.com': 'qwen',
    'api.moonshot.cn': 'kimi', 'api.moonshot.ai': 'kimi',
    'open.bigmodel.cn': 'zhipu', 'api.openai.com': 'openai',
    'api.anthropic.com': 'anthropic', 'generativelanguage.googleapis.com': 'gemini',
}


def detect_provider(base_url: str) -> str | None:
    try:
        host = urlsplit(normalize_url(base_url)).hostname.casefold()
    except (ModelError, AttributeError):
        return None
    if host in _HOSTS:
        return _HOSTS[host]
    # Documented workspace endpoints, not arbitrary aliyuncs.com subdomains.
    labels = host.split('.')
    if (len(labels) == 5 and labels[0] and labels[1] in
            {'cn-beijing', 'ap-southeast-1', 'us-east-1'}
            and labels[2:] == ['maas', 'aliyuncs', 'com']):
        return 'qwen'
    return None
