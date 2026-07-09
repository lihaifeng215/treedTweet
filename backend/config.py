"""
LLM 配置管理模块

配置来源优先级：
1. 前端通过 /api/llm-config 接口写入（运行时，存于内存，可选落盘到 config.json）
2. 后端环境变量（部署时更安全，推荐生产环境使用）
3. 默认值（未配置）

环境变量名：
  LLM_API_KEY           - 大模型 API Key
  LLM_BASE_URL          - 兼容 OpenAI 的 API 基地址，如 https://apihub.agnes-ai.com/v1
  LLM_MODEL             - 模型名称，如 gpt-4o-mini / deepseek-chat
  LLM_TEMPERATURE       - 采样温度（可选，默认 0.8）
  LLM_STREAM_ENABLED    - 是否启用流式输出（true/false，默认 false）
  LLM_THINKING_ENABLED  - 是否启用思考模式（true/false，默认 false）
  LLM_THINKING_BUDGET   - 思考模式预算 token 数（默认 2048）
  LLM_ARTICLE_MAX_LENGTH - 公众号文章最大长度（默认 2000）
  LLM_PROXY             - HTTP/HTTPS 代理地址，如 http://127.0.0.1:7890（可选）

搜索配置环境变量：
  SEARCH_PROVIDER       - 搜索引擎提供商 duckduckgo/serpapi/bing/bing_html（默认 duckduckgo，自动降级到 bing_html）
  SEARCH_API_KEY        - 搜索引擎 API Key（SerpAPI/Bing 需要，DuckDuckGo/Bing HTML 无需）
  SEARCH_BASE_URL       - 搜索引擎自定义 Base URL（可选）

Firecrawl 配置环境变量：
  FIRECRAWL_API_KEY     - Firecrawl 网页抓取 API Key（用于素材采集）

Tavily 搜索配置环境变量：
  TAVILY_API_KEY        - Tavily 搜索 API Key（AI 专用搜索引擎，搜索质量更高）
"""
import json
import os
import re
import stat
from threading import Lock

CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config.json')

# 内置默认（仅当用户与环境变量都未配置时使用）
_DEFAULTS = {
    'api_key': '',
    'base_url': 'https://apihub.agnes-ai.com/v1',
    'model': 'gpt-4o-mini',
    'temperature': 0.8,
    'stream_enabled': False,
    'thinking_enabled': False,
    'thinking_budget_tokens': 2048,
    'article_max_length': 2000,
    'proxy': '',
    'search_provider': 'bing_html',
    'search_api_key': '',
    'search_base_url': '',
    'firecrawl_api_key': '',
    'tavily_api_key': '',
}

_lock = Lock()
_runtime_config = None  # 运行时缓存


def _load_from_disk():
    """从 config.json 读取落盘配置（前端写入的）"""
    try:
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return data
    except Exception:
        pass
    return {}


def _validate_url(url):
    """Basic URL validation."""
    if not url:
        return False
    try:
        pattern = re.compile(
            r'^https?://'
            r'(?:[A-Z0-9_\-\.~]|%[0-9A-Fa-f]{2})+'
            r'(?::\d{1,5})?'
            r'(?:/[A-Z0-9_\-\.~:/?#\[\]@!$&\'()*+,;=-])*'
            r'$', re.IGNORECASE)
        return bool(pattern.match(url))
    except Exception:
        return False


def _clamp(val, lo, hi):
    """Clamp a numeric value to [lo, hi]."""
    try:
        return max(lo, min(hi, float(val)))
    except (TypeError, ValueError):
        return lo


def get_config():
    """合并 默认值 < 磁盘配置 < 环境变量，返回配置字典。线程安全。"""
    global _runtime_config

    # Fast path without lock
    with _lock:
        if _runtime_config is not None:
            return _runtime_config

    # Slow path: build config
    cfg = dict(_DEFAULTS)
    cfg.update(_load_from_disk())

    # 环境变量优先级最高
    env_key = os.environ.get('LLM_API_KEY')
    env_base = os.environ.get('LLM_BASE_URL')
    env_model = os.environ.get('LLM_MODEL')
    env_temp = os.environ.get('LLM_TEMPERATURE')
    env_stream = os.environ.get('LLM_STREAM_ENABLED')
    env_thinking = os.environ.get('LLM_THINKING_ENABLED')
    env_thinking_budget = os.environ.get('LLM_THINKING_BUDGET')
    env_article_len = os.environ.get('LLM_ARTICLE_MAX_LENGTH')
    env_proxy = os.environ.get('LLM_PROXY')
    env_search_provider = os.environ.get('SEARCH_PROVIDER')
    env_search_key = os.environ.get('SEARCH_API_KEY')
    env_search_base = os.environ.get('SEARCH_BASE_URL')
    env_firecrawl_key = os.environ.get('FIRECRAWL_API_KEY')
    env_tavily_key = os.environ.get('TAVILY_API_KEY')

    if env_key:
        cfg['api_key'] = env_key
    if env_base and _validate_url(env_base):
        cfg['base_url'] = env_base
    if env_model:
        cfg['model'] = env_model
    if env_temp:
        cfg['temperature'] = _clamp(env_temp, 0, 2)
    if env_stream is not None:
        cfg['stream_enabled'] = env_stream.lower() in ('true', '1', 'yes')
    if env_thinking is not None:
        cfg['thinking_enabled'] = env_thinking.lower() in ('true', '1', 'yes')
    if env_thinking_budget is not None:
        cfg['thinking_budget_tokens'] = _clamp(env_thinking_budget, 512, 32768)
    if env_article_len is not None:
        cfg['article_max_length'] = _clamp(env_article_len, 500, 8000)
    if env_proxy:
        cfg['proxy'] = env_proxy
    if env_search_provider:
        cfg['search_provider'] = env_search_provider.lower()
    if env_search_key:
        cfg['search_api_key'] = env_search_key
    if env_search_base:
        cfg['search_base_url'] = env_search_base
    if env_firecrawl_key:
        cfg['firecrawl_api_key'] = env_firecrawl_key
    if env_tavily_key:
        cfg['tavily_api_key'] = env_tavily_key

    # Clamp temperature even if not from env
    cfg['temperature'] = _clamp(cfg.get('temperature', 0.8), 0, 2)

    with _lock:
        _runtime_config = cfg
    return cfg


def update_config(new_cfg):
    """更新运行时配置，并落盘保存（供前端 /api/llm-config 调用）"""
    global _runtime_config
    cfg = get_config()
    str_keys = ('api_key', 'base_url', 'model', 'proxy', 'search_provider', 'search_api_key', 'search_base_url', 'firecrawl_api_key', 'tavily_api_key')
    float_keys = ('temperature',)
    bool_keys = ('stream_enabled', 'thinking_enabled')
    int_keys = ('thinking_budget_tokens', 'article_max_length')

    for k in str_keys + float_keys + bool_keys + int_keys:
        if k in new_cfg and new_cfg[k] is not None:
            if k in float_keys:
                cfg[k] = _clamp(new_cfg[k], 0, 2)
            elif k in bool_keys:
                val = new_cfg[k]
                cfg[k] = bool(val) if not isinstance(val, bool) else val
            elif k in int_keys:
                try:
                    cfg[k] = int(new_cfg[k])
                except (TypeError, ValueError):
                    pass
            else:
                cfg[k] = new_cfg[k]

    # Validate base_url
    if cfg.get('base_url') and not _validate_url(cfg['base_url']):
        cfg['base_url'] = _DEFAULTS['base_url']

    # 落盘（先写盘，确保磁盘持久化成功后再更新内存缓存）
    try:
        fd = os.open(CONFIG_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        # 磁盘写入失败，不更新内存缓存（下次仍会从磁盘读取原值）
        return get_config()

    # 磁盘写入成功后，更新内存缓存并清除缓存标记，下次加载时从磁盘重新读取
    with _lock:
        _runtime_config = cfg
    return cfg


def is_configured():
    """是否已配置可用的 API Key"""
    return bool(get_config().get('api_key'))
