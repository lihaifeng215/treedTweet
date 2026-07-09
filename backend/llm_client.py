"""
大模型客户端模块

支持 OpenAI 兼容格式（OpenAI / DeepSeek / 本地 Ollama 等）。
通过 config.py 读取 API Key、Base URL、模型名。

用途：
1. 生成"监控数据"：对热点做智能摘要、关键点提炼、情感与传播分析
2. 生成"公众号文章"：基于热点 + 风格，生成带标题的完整公众号热点文章
"""
import json
import re
from config import get_config, is_configured


def _get_proxies():
    """从配置中构建代理字典"""
    cfg = get_config()
    proxy_url = cfg.get('proxy', '')
    if proxy_url:
        return {'http': proxy_url, 'https': proxy_url}
    return None


# 公众号文章风格定义（system 指令 + 中文风格说明）
def _get_style_instruction(style_key):
    """根据风格和配置生成 system 指令"""
    cfg = get_config()
    max_len = cfg.get('article_max_length', 2000)
    instructions = {
        'professional_depth': (
            f'你是一位资深行业分析师和公众号主笔。请对热点事件进行专业深度解读：'
            f'运用专业知识剖析事件本质，引用权威数据和行业术语，建立严谨的分析框架。'
            f'从商业模式、技术原理、产业格局等维度切入，让读者获得超越表面的洞察。'
            f'文章要有清晰的论点、扎实的论据和可验证的逻辑链条。'
            f'正文控制在 {max_len} 字左右，结构清晰，段落分明。'
        ),
        'humorous': (
            f'你是一位幽默风趣的公众号创作者。请用诙谐调侃的笔触解读热点：'
            f'善用段子、比喻、反讽和网络热梗，让读者在笑声中看懂事件本质。'
            f'风格轻松但不肤浅，笑点背后藏着洞察，金句频出且易于传播。'
            f'语言鲜活接地气，节奏明快，拒绝说教。'
            f'正文控制在 {max_len} 字左右，结构清晰，段落分明。'
        ),
        'suspenseful': (
            f'你是一位擅长制造悬念的公众号写手。请用悬念式叙事吸引读者一口气读完：'
            f'开头抛出一个引人好奇的问题或反常现象，逐层剥开真相。'
            f'善用设问、转折、伏笔和反转，制造"竟然是这样"的阅读爽感。'
            f'节奏紧凑，每个段落都要埋下继续阅读的钩子，结尾有力收束。'
            f'正文控制在 {max_len} 字左右，结构清晰，段落分明。'
        ),
        'emotional': (
            f'你是一位善于引发情感共鸣的公众号作者。请从情感视角切入热点：'
            f'捕捉事件中触动人心的人物故事、情感细节和普世价值。'
            f'用温暖、真诚、有温度的笔触讲述，让读者产生"说的就是我"的共鸣。'
            f'注重情感层次的铺陈，从个体经历升华到群体感受，结尾留有回味。'
            f'正文控制在 {max_len} 字左右，结构清晰，段落分明。'
        ),
    }
    return instructions.get(style_key, instructions['professional_depth'])


STYLES = {
    'professional_depth': {'name': '专业深度'},
    'humorous': {'name': '幽默风趣'},
    'suspenseful': {'name': '悬念吸引'},
    'emotional': {'name': '情感共鸣'},
}

VALID_STYLES = list(STYLES.keys())


def _build_payload(messages, temperature=None, expect_json=False, stream=False, thinking_enabled=False):
    """构建 API 请求 payload，合并 stream 和 thinking 参数"""
    cfg = get_config()

    payload = {
        'model': cfg['model'],
        'messages': messages,
        'temperature': max(0, min(2, temperature if temperature is not None else cfg['temperature'])),
    }

    if stream:
        payload['stream'] = True

    # 思考模式：某些 API 不支持 response_format 与 thinking 同时使用
    if expect_json and not stream and not thinking_enabled:
        payload['response_format'] = {'type': 'json_object'}

    # 思考模式：仅添加 enable_thinking 标记，不添加特定 provider 的 thinking 结构
    # 这样可以兼容更多 OpenAI 兼容 API（DeepSeek、Agnes 等）
    if thinking_enabled:
        payload['chat_template_kwargs'] = {
            'enable_thinking': True,
        }

    return payload


def _chat(messages, temperature=None, expect_json=False, stream=False, thinking_enabled=False):
    """
    调用 OpenAI 兼容 /chat/completions 接口。
    返回 (ok, content_or_error)
    """
    cfg = get_config()
    if not cfg.get('api_key'):
        return False, 'LLM 未配置：请先在「设置」页配置 API Key 与 Base URL'

    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    payload = _build_payload(messages, temperature, expect_json, stream, thinking_enabled)

    headers = {
        'Authorization': f"Bearer {cfg['api_key']}",
        'Content-Type': 'application/json',
    }

    try:
        import requests
        proxies = _get_proxies()
        resp = requests.post(url, json=payload, headers=headers, timeout=60, proxies=proxies)
        if resp.status_code != 200:
            resp.encoding = 'utf-8'
            return False, f'API 返回错误 {resp.status_code}: {resp.text[:300]}'
        # 显式指定 UTF-8 编码，避免 requests 自动检测出错导致中文乱码
        resp.encoding = 'utf-8'
        data = resp.json()
        content = data['choices'][0]['message']['content']
        return True, content
    except KeyError:
        return False, 'API 响应格式异常，未找到 choices[0].message.content'
    except Exception as e:
        return False, f'调用失败：{str(e)}'


def _chat_stream(messages, temperature=None, thinking_enabled=False):
    """
    流式调用 /chat/completions 接口，通过 SSE 逐步返回内容。
    这是一个生成器，yield 每个 chunk 的文本增量。

    支持思考模式（thinking_enabled）：当模型处于思考阶段时返回 reasoning_content，
    标记为 type='thinking'；实际内容标记为 type='content'。
    """
    cfg = get_config()
    if not cfg.get('api_key'):
        yield 'data: ' + json.dumps({'error': 'LLM 未配置'}) + '\n\n'
        return

    url = cfg['base_url'].rstrip('/') + '/chat/completions'
    payload = _build_payload(messages, temperature, stream=True, thinking_enabled=thinking_enabled)

    headers = {
        'Authorization': f"Bearer {cfg['api_key']}",
        'Content-Type': 'application/json',
    }

    MAX_CONTENT = 50000  # 50KB 上限防止内存泄漏

    try:
        import requests
        proxies = _get_proxies()
        resp = requests.post(url, json=payload, headers=headers, timeout=180, stream=True, proxies=proxies)
        # 显式指定 UTF-8 编码，避免流式响应中文乱码
        resp.encoding = 'utf-8'
        if resp.status_code != 200:
            yield 'data: ' + json.dumps({'error': f'API 返回错误 {resp.status_code}'}) + '\n\n'
            yield 'data: [DONE]\n\n'
            return

        for line in resp.iter_lines(decode_unicode=True):
            if not line or not line.startswith('data: '):
                continue
            data_str = line[6:]
            if data_str == '[DONE]':
                yield 'data: [DONE]\n\n'
                break
            try:
                chunk = json.loads(data_str)
                delta = chunk.get('choices', [{}])[0].get('delta', {})
                content = delta.get('content', '')
                reasoning = delta.get('reasoning_content', '')

                if reasoning:
                    # 思考阶段的 token，标记为 thinking 类型
                    yield 'data: ' + json.dumps({'type': 'thinking', 'content': reasoning}) + '\n\n'
                if content:
                    yield 'data: ' + json.dumps({'type': 'content', 'content': content}) + '\n\n'
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
    except Exception as e:
        yield 'data: ' + json.dumps({'error': str(e)}) + '\n\n'
        yield 'data: [DONE]\n\n'


def test_connection():
    """测试大模型连接是否可用，返回 (ok, message)"""
    if not is_configured():
        return False, '尚未配置 API Key'
    cfg = get_config()
    ok, content = _chat([
        {'role': 'system', 'content': '你是测试助手，只需回复 OK 两个字母。'},
        {'role': 'user', 'content': '回复 OK'},
    ], temperature=0, thinking_enabled=cfg.get('thinking_enabled', False))
    if not ok:
        return False, content
    return True, '连接成功，模型可用 ✅'


def _build_material(hotspot):
    """从热点对象中抽取用于 LLM 的文本素材"""
    title = hotspot.get('title', '未知话题')
    desc = (hotspot.get('desc') or hotspot.get('body') or '')[:400]
    source = hotspot.get('source', '')
    heat = hotspot.get('heat') or hotspot.get('engagement') or ''
    return title, desc, source, heat


def _build_prompts(hotspot, style):
    """构建 LLM 请求的系统提示和用户提示（复用 generate_article 和 generate_article_stream）"""
    if style not in STYLES:
        style = 'professional_depth'

    cfg = get_config()
    max_len = cfg.get('article_max_length', 2000)
    instruction = _get_style_instruction(style)
    title_raw, desc, source, heat = _build_material(hotspot)

    system_prompt = (
        '你是一位资深微信公众号主笔，擅长将热点事件转化为高质量公众号文章。\n'
        + instruction + '\n'
        '请按以下格式输出：\n'
        '第一行：TITLE: 你的文章标题（10-30字，有吸引力）\n'
        '从第二行开始：文章正文（含导语和分段，可使用小标题、**加粗**、emoji 等排版元素）\n'
        f'正文约 {max_len} 字。'
    )

    user_prompt = (
        f"热点标题：{title_raw}\n"
        f"来源：{source}\n"
        f"热度：{heat}\n"
        f"描述：{desc}\n\n"
        f"请基于以上热点，生成一篇「{STYLES[style]['name']}」风格的公众号文章。"
    )

    return system_prompt, user_prompt


# ============================================================
# 1) 监控数据：对热点做智能分析
# ============================================================
_MONITOR_SYSTEM = (
    '你是一个热点舆情监控分析师。给定一条热点，请输出 JSON 格式的结构化监控数据，'
    '字段如下：\n'
    '{\n'
    '  "summary": "用 1-2 句话概括该热点的核心事实",\n'
    '  "key_points": ["关键点1", "关键点2", "关键点3"],\n'
    '  "sentiment": "正面/中性/负面/争议",\n'
    '  "topics": ["相关话题标签1", "相关话题标签2"],\n'
    '  "trend": "上升/平稳/下降",\n'
    '  "audience": "适合关注的受众群体描述",\n'
    '  "relevance": 0-100 的整数，代表内容创作价值\n'
    '}\n'
    '只输出 JSON，不要包含任何解释性文字或 markdown 代码块标记。'
)


def monitor_hotspot(hotspot):
    """调用 LLM 生成热点的结构化监控数据，返回 dict（失败返回基础结构）"""
    cfg = get_config()
    title, desc, source, heat = _build_material(hotspot)
    user_prompt = (
        f"热点标题：{title}\n"
        f"来源：{source}\n"
        f"热度：{heat}\n"
        f"描述：{desc}\n\n"
        "请基于以上信息生成监控分析数据。"
    )

    ok, content = _chat([
        {'role': 'system', 'content': _MONITOR_SYSTEM},
        {'role': 'user', 'content': user_prompt},
    ], expect_json=True, temperature=0.4, thinking_enabled=False)

    if not ok:
        return {'error': content, 'summary': title, 'key_points': [], 'sentiment': '未知',
                'topics': [], 'trend': '未知', 'audience': '', 'relevance': 0}

    parsed = _safe_parse_json(content)
    if parsed is None:
        return {'error': 'LLM 返回内容无法解析', 'summary': title, 'key_points': [],
                'sentiment': '未知', 'topics': [], 'trend': '未知', 'audience': '', 'relevance': 0}
    parsed.setdefault('summary', title)
    parsed.setdefault('key_points', [])
    parsed.setdefault('sentiment', '未知')
    parsed.setdefault('topics', [])
    parsed.setdefault('trend', '未知')
    parsed.setdefault('audience', '')
    parsed.setdefault('relevance', 0)
    return parsed


# ============================================================
# 2) 公众号文章：生成带标题的完整热点文章
# ============================================================
def generate_article(hotspot, style='professional_depth'):
    """
    调用 LLM 基于热点 + 风格生成一篇完整的公众号文章。
    使用 TITLE: 行格式（与流式模式一致），避免 json_object 兼容性问题。
    返回 (ok, dict with title+content or error_message)
    """
    system_prompt, user_prompt = _build_prompts(hotspot, style)
    cfg = get_config()

    ok, content = _chat([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ], thinking_enabled=cfg.get('thinking_enabled', False))

    if not ok:
        return False, content

    # 解析 TITLE: 行和正文
    text = content.strip()
    lines = text.split('\n', 1)
    first_line = lines[0].strip()
    if first_line.upper().startswith('TITLE:'):
        article_title = first_line[6:].strip()
        article_content = lines[1].strip() if len(lines) > 1 else ''
    else:
        # fallback: 尝试整段作为正文，标题用热点标题
        article_title = hotspot.get('title', '未知话题')
        article_content = text

    if not article_content:
        return False, 'LLM 未生成有效文章内容'

    return True, {'title': article_title, 'content': article_content}


def _parse_title_from_text(text):
    """从文本中提取 TITLE: 行，返回 (title, body_text)。
    支持 TITLE: 或 title: 前缀，处理可能的思考 token 混入。"""
    text = text.strip()
    if not text:
        return None, ''
    # 使用正则匹配 TITLE: 行（不区分大小写），允许前后有空格
    m = re.match(r'(?i)^\s*TITLE\s*:\s*(.+?)(?:\r?\n|$)', text)
    if m:
        title = m.group(1).strip()
        # 去掉 TITLE 行，剩余为正文
        body = re.sub(r'(?i)^\s*TITLE\s*:.*?(?:\r?\n|$)', '', text, count=1).strip()
        return title, body
    return None, text


def generate_article_stream(hotspot, style='professional_depth'):
    """
    流式生成公众号文章。先输出标题，再逐步输出正文。
    这是一个 Python 生成器，yield dict: 
      {'type': 'title', 'content': '...'} 
    | {'type': 'chunk', 'content': '...'} 
    | {'type': 'thinking', 'message': '...'} 
    | {'type': 'done', 'article': {...}}
    | {'type': 'error', 'message': '...'}
    """
    system_prompt, user_prompt = _build_prompts(hotspot, style)
    cfg = get_config()
    title_raw = hotspot.get('title', '未知话题')

    full_text = ''
    parsed_title = None
    thinking_received = False

    for sse_line in _chat_stream([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ], thinking_enabled=cfg.get('thinking_enabled', False)):
        if sse_line.startswith('data: '):
            data_str = sse_line[6:]
            if data_str == '[DONE]':
                # 流结束，解析标题和正文
                if not parsed_title:
                    # 尝试从完整文本中检测 TITLE: 行
                    lines = full_text.split('\n', 1)
                    first_line = lines[0].strip()
                    if first_line.upper().startswith('TITLE:'):
                        parsed_title = first_line[6:].strip()
                        full_text = lines[1].strip() if len(lines) > 1 else ''
                    else:
                        parsed_title = title_raw

                article = {
                    'title': parsed_title or title_raw,
                    'content': full_text.strip(),
                }
                yield {'type': 'done', 'article': article}
                return
            try:
                chunk = json.loads(data_str)
                if 'error' in chunk:
                    yield {'type': 'error', 'message': chunk['error']}
                    return

                chunk_type = chunk.get('type', 'content')  # 兼容旧格式
                chunk_content = chunk.get('content', '')

                if chunk_type == 'thinking' and chunk_content:
                    # 思考阶段：通知前端模型正在思考，不累积到正文
                    if not thinking_received:
                        thinking_received = True
                        yield {'type': 'thinking', 'message': '模型正在思考...'}
                    continue

                if chunk_type == 'content' and chunk_content:
                    full_text += chunk_content

                    # 尝试从流式文本中检测 TITLE: 行
                    if parsed_title is None and '\n' in full_text:
                        first_line, rest = full_text.split('\n', 1)
                        if first_line.upper().startswith('TITLE:'):
                            parsed_title = first_line[6:].strip()
                            yield {'type': 'title', 'content': parsed_title}
                            # body 部分是 rest，后续只累积 body
                            if rest.strip():
                                yield {'type': 'chunk', 'content': rest}
                            full_text = rest
                            # 当前 chunk 已在 rest 中发送，跳过后续 yield
                            continue

                    # 发送正文增量（标题已解析后）
                    if parsed_title is not None:
                        yield {'type': 'chunk', 'content': chunk_content}
            except Exception:
                continue

    # 如果流没有正常结束（fallback）
    if not parsed_title:
        lines = full_text.split('\n', 1)
        first_line = lines[0].strip()
        if first_line.upper().startswith('TITLE:'):
            parsed_title = first_line[6:].strip()
            full_text = lines[1].strip() if len(lines) > 1 else ''
        else:
            parsed_title = title_raw

    article = {
        'title': parsed_title or title_raw,
        'content': full_text.strip(),
    }
    yield {'type': 'done', 'article': article}


def generate_multi_hotspot_article(hotspots, style='professional_depth'):
    """
    基于多个热点合成一篇公众号文章（日报/周报风格）。
    返回 (ok, dict with title+content or error_message)
    """
    if style not in STYLES:
        style = 'professional_depth'

    cfg = get_config()
    max_len = cfg.get('article_max_length', 2000)

    # 构建多热点素材
    hotspot_texts = []
    for i, hs in enumerate(hotspots[:5]):
        title = hs.get('title', '')
        source = hs.get('source', '')
        desc = (hs.get('desc') or hs.get('body') or '')[:200]
        heat = hs.get('heat') or hs.get('engagement') or ''
        hotspot_texts.append(
            f"热点{i+1}：{title}\n  来源：{source}\n  热度：{heat}\n  描述：{desc}"
        )
    material = "\n\n".join(hotspot_texts)

    system_prompt = (
        '你是一位资深微信公众号主笔，擅长将多条热点事件整合为一篇高质量的热点综述文章。\\n'
        + _get_style_instruction(style) + '\\n'
        '文章要求：\\n'
        '1. 标题要有概括性和吸引力，能涵盖所有热点主题\\n'
        '2. 开篇用一段导语总览今日/本周热点全景\\n'
        '3. 正文按逻辑分组热点事件，每个热点独立成段但要有内在联系\\n'
        '4. 在段落之间穿插你的分析和评论，让读者看到事件背后的关联\\n'
        '5. 结尾总结展望，给出一个有力的收尾\\n'
        '请按以下格式输出：\\n'
        '第一行：TITLE: 你的文章标题（15-40字，有吸引力）\\n'
        '从第二行开始：文章正文（含导语、分段、小标题、结语）\\n'
        f'正文约 {max_len} 字。'
    )

    user_prompt = (
        f'以下是 {len(hotspots)} 条今日热点事件，请整合为一篇公众号综述文章：\\n\\n'
        + material + '\\n\\n'
        f'请使用「{STYLES[style]["name"]}」风格撰写。'
    )

    ok, content = _chat([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ], thinking_enabled=cfg.get('thinking_enabled', False))

    if not ok:
        return False, content

    text = content.strip()
    lines = text.split('\n', 1)
    first_line = lines[0].strip()
    if first_line.upper().startswith('TITLE:'):
        article_title = first_line[6:].strip()
        article_content = lines[1].strip() if len(lines) > 1 else ''
    else:
        article_title = f'今日热点综述：{len(hotspots)}件事值得关注'
        article_content = text

    if not article_content:
        return False, 'LLM 未生成有效文章内容'

    return True, {'title': article_title, 'content': article_content}


def _safe_parse_json(content):
    """从 LLM 返回中安全解析 JSON（容忍 ```json 代码块包裹）"""
    if not content:
        return None
    content = content.strip()
    # 去掉 ```json ... ``` 包裹
    m = re.search(r'```(?:json)?\s*(.*?)\s*```', content, re.DOTALL)
    if m:
        content = m.group(1).strip()
    try:
        return json.loads(content)
    except Exception:
        pass
    # 尝试截取第一个 { 到最后一个 } 之间的内容
    s = content.find('{')
    e = content.rfind('}')
    if s != -1 and e != -1 and e > s:
        try:
            return json.loads(content[s:e + 1])
        except Exception:
            return None
    return None


# ============================================================
# 渐进式创作工作流（6 步）
# ============================================================

# 工作流写作风格映射（复用现有 4 风格，提供中文名给工作流使用）
WORKFLOW_STYLES = {
    'professional_depth': '专业深度',
    'humorous': '幽默风趣',
    'suspenseful': '悬念吸引',
    'emotional': '情感共鸣',
}


def _build_material_text(source_data, source):
    """从素材数据构建供 LLM 使用的文本描述"""
    if source == 'hotspot':
        title = source_data.get('title', '未知话题')
        desc = (source_data.get('desc') or source_data.get('body') or '')[:500]
        src = source_data.get('source', '')
        heat = source_data.get('heat') or source_data.get('engagement') or ''
        return f"热点标题：{title}\n来源：{src}\n热度：{heat}\n描述：{desc}"
    else:
        topic = source_data.get('topic', '')
        context = source_data.get('context', '')
        text = f"主题：{topic}"
        if context:
            text += f"\n补充素材：{context[:500]}"
        return text


# ---- 步骤1：素材解析 ----
_PARSE_SYSTEM = (
    '你是一位资深内容策划分析师。请对用户提供的素材进行深度解析，输出 JSON 格式：\n'
    '{\n'
    '  "concepts": ["核心概念1", "核心概念2"],  // 2-4个核心概念\n'
    '  "audience": "目标受众群体描述",  // 1句话\n'
    '  "angles": ["切入角度1", "切入角度2", "切入角度3"],  // 3个可选的创作角度\n'
    '  "keywords": ["关键词1", "关键词2", "关键词3"],  // 3-5个用于搜索的关键词\n'
    '  "summary": "素材核心要点的1-2句话概括"\n'
    '}\n'
    '只输出 JSON，不要包含任何解释性文字或 markdown 代码块标记。'
)


def parse_material(source_data, source='custom'):
    """步骤1：LLM 素材解析。返回 (ok, dict or error_message)"""
    material_text = _build_material_text(source_data, source)
    ok, content = _chat([
        {'role': 'system', 'content': _PARSE_SYSTEM},
        {'role': 'user', 'content': f'请解析以下素材：\n\n{material_text}'},
    ], expect_json=True, temperature=0.4, thinking_enabled=False)

    if not ok:
        return False, content
    parsed = _safe_parse_json(content)
    if parsed is None:
        return False, '素材解析结果无法解析为 JSON'
    parsed.setdefault('concepts', [])
    parsed.setdefault('audience', '')
    parsed.setdefault('angles', [])
    parsed.setdefault('keywords', [])
    parsed.setdefault('summary', '')
    return True, parsed


# ---- 步骤2：搜索计划 ----
_SEARCH_PLAN_SYSTEM = (
    '你是一位搜索策略专家。基于素材解析结果，生成 3-5 个高质量的搜索查询语句，'
    '用于收集创作所需的背景资料。输出 JSON 格式：\n'
    '{\n'
    '  "search_queries": ["搜索词1", "搜索词2", "搜索词3"],  // 3-5个搜索查询，中文\n'
    '  "rationale": "搜索策略说明"\n'
    '}\n'
    '只输出 JSON，不要包含任何解释性文字或 markdown 代码块标记。'
)


def generate_search_plan(parsed_material):
    """步骤2：基于素材解析结果生成搜索计划。返回 (ok, dict or error_message)"""
    concepts = parsed_material.get('concepts', [])
    keywords = parsed_material.get('keywords', [])
    audience = parsed_material.get('audience', '')
    angles = parsed_material.get('angles', [])

    user_prompt = (
        f"核心概念：{', '.join(concepts)}\n"
        f"关键词：{', '.join(keywords)}\n"
        f"目标受众：{audience}\n"
        f"切入角度：{', '.join(angles)}\n\n"
        f"请生成 3-5 个搜索查询语句，覆盖事件背景、数据佐证、不同观点等维度。"
    )
    ok, content = _chat([
        {'role': 'system', 'content': _SEARCH_PLAN_SYSTEM},
        {'role': 'user', 'content': user_prompt},
    ], expect_json=True, temperature=0.5, thinking_enabled=False)

    if not ok:
        return False, content
    parsed = _safe_parse_json(content)
    if parsed is None:
        return False, '搜索计划无法解析为 JSON'
    queries = parsed.get('search_queries', [])
    if not isinstance(queries, list) or not queries:
        # fallback: 用关键词作为搜索词
        queries = keywords[:3] if keywords else [parsed_material.get('summary', '')[:20]]
        parsed['search_queries'] = queries
    return True, parsed


# ---- 步骤2b：研究简报合成（基于深度抓取内容）----
_RESEARCH_BRIEF_SYSTEM = (
    '你是一位资深信息分析师。基于多个网页的完整内容和搜索结果，生成一份结构化的研究简报。\n'
    '你的任务是：提炼关键事实、数据、不同观点，确保每一个论断都有信息来源支撑。\n'
    '输出 JSON 格式：\n'
    '{\n'
    '  "brief": "综合研究摘要（300-500字，概述事件全貌、关键背景、核心争议或焦点）",\n'
    '  "key_facts": [\n'
    '    {"fact": "关键事实描述", "source": "来源标题或URL简称"},\n'
    '    ...\n'
    '  ],\n'
    '  "data_points": [\n'
    '    {"data": "具体数据/统计", "source": "来源标题"},\n'
    '    ...\n'
    '  ],\n'
    '  "different_perspectives": [\n'
    '    {"perspective": "某方观点或立场描述", "source": "来源标题"},\n'
    '    ...\n'
    '  ],\n'
    '  "timeline": [\n'
    '    {"time": "时间点", "event": "事件描述"},\n'
    '    ...\n'
    '  ],\n'
    '  "credibility_assessment": "对信息来源可信度的整体评估（50-100字）",\n'
    '  "knowledge_gaps": ["信息缺口1：哪些关键问题尚未找到答案", ...]\n'
    '}\n'
    '要求：\n'
    '1. 每个事实/数据必须注明来源\n'
    '2. 如果某维度无相关信息，返回空数组\n'
    '3. 不要编造任何信息，只基于提供的网页内容\n'
    '4. 只输出 JSON，不要包含任何解释性文字或 markdown 代码块标记。'
)


def synthesize_research_brief(enriched_results, parsed_material, thinking_enabled=False):
    """
    步骤2b：基于搜索结果 + 深度抓取内容，合成结构化的研究简报。

    Args:
        enriched_results: list of {
            url, title,
            snippet: 搜索引擎返回的内容（Tavily: 1500-2300字正文; Bing/DuckDuckGo: ~100字摘要）
            firecrawl_markdown: Firecrawl 抓取的完整网页 Markdown（可选）
            score: Tavily 相关性评分（可选）
        }
        parsed_material: 步骤1 的素材解析结果

    Returns:
        (ok: bool, brief: dict|str)
    """
    # 按信息量分级构建每项的内容
    contents_text = ''
    has_deep = False   # 是否有 Firecrawl 深度抓取
    has_tavily = False  # 是否有 Tavily 长内容
    used_count = 0

    for i, item in enumerate(enriched_results[:15]):
        title = item.get('title', '无标题')
        url = item.get('url', '')
        firecrawl = item.get('firecrawl_markdown', '')
        snippet = item.get('snippet', '')

        source_block = f'\n--- 来源 {i+1}: {title} ---\nURL: {url}\n'

        if firecrawl and len(firecrawl) > 100:
            # 级别1：有 Firecrawl 全文 → 作为主内容
            has_deep = True
            truncated = firecrawl[:3500]
            if len(firecrawl) > 3500:
                truncated += f'\n...（原文共 {len(firecrawl)} 字，已截断）'
            source_block += f'正文（Firecrawl 深度抓取）：\n{truncated}\n'
            # 如果有 Tavily 长内容，作为 AI 摘要附上（帮助 LLM 抓住重点）
            if snippet and len(snippet) > 300:
                source_block += f'\nAI 提取摘要（Tavily，辅助参考）：{snippet[:600]}\n'
            used_count += 1
        elif snippet and len(snippet) > 300:
            # 级别2：无 Firecrawl 但有 Tavily 长内容 → 直接用完整内容
            has_tavily = True
            # Tavily 内容 1500-2300 字，不截断
            source_block += f'正文（搜索引擎提取）：\n{snippet}\n'
            used_count += 1
        elif snippet:
            # 级别3：只有短摘要 → 保留完整摘要（不截断到 200！）
            source_block += f'摘要：{snippet}\n'
            used_count += 1
        else:
            continue

        contents_text += source_block

    if not used_count:
        return False, '没有可用的调研资料（无抓取内容且无搜索结果）'

    # 根据信息质量选择提示语
    if has_deep:
        mode_hint = '深度模式：以下内容来自 Firecrawl 完整网页抓取 + 搜索引擎提取，信息完整度高。'
    elif has_tavily:
        mode_hint = '增强模式：以下内容来自 Tavily 搜索引擎提取的完整正文（非简短摘要），信息丰富。请充分利用这些内容，在简报中引用具体事实和数据。'
    else:
        mode_hint = '摘要模式：以下内容为传统搜索引擎摘要片段，信息有限。请在简报中标注不确定的部分。'

    user_prompt = (
        f"事件主题：{parsed_material.get('summary', '未知')}\n"
        f"核心概念：{', '.join(parsed_material.get('concepts', []))}\n"
        f"目标受众：{parsed_material.get('audience', '')}\n\n"
        f"------------------\n"
        f"{mode_hint}\n"
        f"------------------\n\n"
        f"{contents_text}\n\n"
        f"请基于以上内容生成结构化的研究简报。"
    )

    ok, content = _chat([
        {'role': 'system', 'content': _RESEARCH_BRIEF_SYSTEM},
        {'role': 'user', 'content': user_prompt},
    ], expect_json=True, temperature=0.4, thinking_enabled=thinking_enabled)

    if not ok:
        return False, content

    parsed = _safe_parse_json(content)
    if parsed is None:
        return False, '研究简报无法解析为 JSON'

    # 确保字段存在
    parsed.setdefault('brief', '')
    parsed.setdefault('key_facts', [])
    parsed.setdefault('data_points', [])
    parsed.setdefault('different_perspectives', [])
    parsed.setdefault('timeline', [])
    parsed.setdefault('credibility_assessment', '')
    parsed.setdefault('knowledge_gaps', [])

    return True, parsed


# ---- 步骤3：选题讨论 ----
_TOPICS_SYSTEM = (
    '你是一位资深公众号选题策划师。基于素材解析和研究简报，生成 3-5 个候选选题。'
    '每个选题应有独特的切入角度，必须基于研究简报中的真实信息，不可凭空想象。'
    '输出 JSON 格式：\n'
    '{\n'
    '  "topics": [\n'
    '    {\n'
    '      "id": "topic_1",\n'
    '      "title": "选题标题（15-30字，有吸引力）",\n'
    '      "angle": "切入角度简述",\n'
    '      "summary": "选题摘要，说明将如何展开（2-3句话）",\n'
    '      "evidence_base": "支撑该选题的关键事实/数据（引用研究简报中的内容）"\n'
    '    }\n'
    '  ]\n'
    '}\n'
    '只输出 JSON，不要包含任何解释性文字或 markdown 代码块标记。'
)


def generate_topics(parsed_material, research):
    """步骤3：生成候选选题。返回 (ok, dict or error_message)"""
    concepts = parsed_material.get('concepts', [])
    angles = parsed_material.get('angles', [])

    # 优先使用研究简报，其次使用搜索结果摘要
    brief = research.get('brief', {})
    if brief and brief.get('brief'):
        # 有研究简报，使用简报内容
        research_text = f"研究摘要：{brief.get('brief', '')}\n\n"

        key_facts = brief.get('key_facts', [])
        if key_facts:
            research_text += '关键事实：\n'
            for f in key_facts[:8]:
                fact_text = f.get('fact', f) if isinstance(f, dict) else str(f)
                source = f.get('source', '') if isinstance(f, dict) else ''
                research_text += f"  · {fact_text}"
                if source:
                    research_text += f"（来源：{source}）"
                research_text += '\n'

        data_points = brief.get('data_points', [])
        if data_points:
            research_text += '\n关键数据：\n'
            for d in data_points[:5]:
                data_text = d.get('data', d) if isinstance(d, dict) else str(d)
                source = d.get('source', '') if isinstance(d, dict) else ''
                research_text += f"  · {data_text}"
                if source:
                    research_text += f"（来源：{source}）"
                research_text += '\n'

        perspectives = brief.get('different_perspectives', [])
        if perspectives:
            research_text += '\n不同观点：\n'
            for p in perspectives[:3]:
                p_text = p.get('perspective', p) if isinstance(p, dict) else str(p)
                research_text += f"  · {p_text}\n"

        knowledge_gaps = brief.get('knowledge_gaps', [])
        if knowledge_gaps:
            research_text += f"\n信息缺口：{'；'.join(knowledge_gaps[:3])}\n"
    else:
        # fallback：使用搜索结果（不截断，Tavily 可能返回完整正文）
        research_items = research.get('results', [])
        research_text = '⚠️ 注意：以下为搜索结果内容，信息可能不完整，选题时请标注不确定性。\n\n'
        for i, item in enumerate(research_items[:8]):
            snip = item.get('snippet', '')
            research_text += f"{i+1}. [{item.get('title', '')}]\n"
            if snip:
                research_text += f"   {snip}\n"

    user_prompt = (
        f"核心概念：{', '.join(concepts)}\n"
        f"可选角度：{', '.join(angles)}\n\n"
        f"研究简报：\n{research_text}\n\n"
        f"请严格基于以上研究简报中的真实信息生成 3-5 个差异化的候选选题，"
        f"每个选题的 evidence_base 字段必须引用简报中的具体事实。"
    )
    ok, content = _chat([
        {'role': 'system', 'content': _TOPICS_SYSTEM},
        {'role': 'user', 'content': user_prompt},
    ], expect_json=True, temperature=0.7, thinking_enabled=False)

    if not ok:
        return False, content
    parsed = _safe_parse_json(content)
    if parsed is None:
        return False, '选题结果无法解析为 JSON'
    topics = parsed.get('topics', [])
    if not isinstance(topics, list) or not topics:
        return False, 'LLM 未生成有效选题'
    # 确保每个选题有 id
    for i, t in enumerate(topics):
        if not t.get('id'):
            t['id'] = f'topic_{i+1}'
    return True, parsed


# ---- 步骤4：大纲确认 ----
_OUTLINE_SYSTEM = (
    '你是一位资深公众号文章结构设计师。基于选定选题和调研资料，生成详细的文章大纲。'
    '输出 JSON 格式：\n'
    '{\n'
    '  "title": "文章标题（10-30字，有吸引力）",\n'
    '  "sections": [\n'
    '    {\n'
    '      "heading": "章节标题",\n'
    '      "points": ["要点1", "要点2"],  // 该章节要阐述的要点\n'
    '      "estimated_words": 300  // 该章节预估字数\n'
    '    }\n'
    '  ],\n'
    '  "total_estimated_words": 2000\n'
    '}\n'
    '大纲应包含：导语、2-4个正文段落、结语。只输出 JSON，不要包含任何解释性文字或 markdown 代码块标记。'
)


def generate_outline(topic, parsed_material, research, style='professional_depth', max_length=None):
    """步骤4：生成大纲。返回 (ok, dict or error_message)"""
    cfg = get_config()
    if max_length is None:
        max_length = cfg.get('article_max_length', 2000)
    style_name = WORKFLOW_STYLES.get(style, '专业深度')

    # 优先使用研究简报
    brief = research.get('brief', {})
    if brief and brief.get('brief'):
        research_text = f"研究摘要：{brief.get('brief', '')}\n\n"
        key_facts = brief.get('key_facts', [])
        if key_facts:
            research_text += '关键事实：\n'
            for f in key_facts[:6]:
                fact_text = f.get('fact', f) if isinstance(f, dict) else str(f)
                research_text += f"  · {fact_text}\n"
        data_points = brief.get('data_points', [])
        if data_points:
            research_text += '\n关键数据：\n'
            for d in data_points[:4]:
                data_text = d.get('data', d) if isinstance(d, dict) else str(d)
                research_text += f"  · {data_text}\n"
        perspectives = brief.get('different_perspectives', [])
        if perspectives:
            research_text += '\n不同观点：\n'
            for p in perspectives[:3]:
                p_text = p.get('perspective', p) if isinstance(p, dict) else str(p)
                research_text += f"  · {p_text}\n"
    else:
        research_items = research.get('results', [])
        research_text = '⚠️ 注意：以下为搜索结果内容，信息可能不完整。\n'
        for i, item in enumerate(research_items[:6]):
            snip = item.get('snippet', '')
            research_text += f"{i+1}. [{item.get('title', '')}]\n"
            if snip:
                research_text += f"   {snip}\n"

    concepts = parsed_material.get('concepts', [])
    audience = parsed_material.get('audience', '')

    user_prompt = (
        f"选定选题：{topic.get('title', '')}\n"
        f"选题角度：{topic.get('angle', '')}\n"
        f"选题摘要：{topic.get('summary', '')}\n\n"
        f"核心概念：{', '.join(concepts)}\n"
        f"目标受众：{audience}\n"
        f"写作风格：{style_name}\n"
        f"目标字数：约 {max_length} 字\n\n"
        f"研究简报：\n{research_text}\n\n"
        f"请基于研究简报中的真实信息生成详细的文章大纲。"
        f"大纲的每个章节要点必须能对应到简报中的具体事实或数据。"
    )
    ok, content = _chat([
        {'role': 'system', 'content': _OUTLINE_SYSTEM},
        {'role': 'user', 'content': user_prompt},
    ], expect_json=True, temperature=0.6, thinking_enabled=False)

    if not ok:
        return False, content
    parsed = _safe_parse_json(content)
    if parsed is None:
        return False, '大纲无法解析为 JSON'
    if not parsed.get('title'):
        parsed['title'] = topic.get('title', '未命名文章')
    if not isinstance(parsed.get('sections'), list):
        parsed['sections'] = []
    return True, parsed


# ---- 步骤5：正文生成（流式）----
def generate_body_stream(outline, style='professional_depth', research=None, max_length=None):
    """
    步骤5：基于大纲流式生成完整正文。
    这是一个 Python 生成器，yield dict:
      {'type': 'title', 'content': '...'}
    | {'type': 'chunk', 'content': '...'}
    | {'type': 'thinking', 'message': '...'}
    | {'type': 'done', 'article': {title, content}}
    | {'type': 'error', 'message': '...'}
    """
    cfg = get_config()
    if max_length is None:
        max_length = cfg.get('article_max_length', 2000)
    style_name = WORKFLOW_STYLES.get(style, '专业深度')
    instruction = _get_style_instruction(style)

    title = outline.get('title', '未命名文章')
    sections = outline.get('sections', [])

    # 构建大纲文本
    outline_text = f"文章标题：{title}\n"
    for i, sec in enumerate(sections):
        heading = sec.get('heading', f'第{i+1}节')
        points = sec.get('points', [])
        est = sec.get('estimated_words', '')
        outline_text += f"\n{i+1}. {heading}"
        if est:
            outline_text += f"（约{est}字）"
        if points:
            outline_text += "\n   要点：" + "；".join(points)

    # 调研资料：优先使用研究简报 + 可引用资料链接
    research_context = ''
    if research:
        brief = research.get('brief', {})
        if brief and brief.get('brief'):
            research_context = f"\n\n研究简报（基于真实信息，请严格引用）：\n{brief.get('brief', '')}\n\n关键事实：\n"
            for f in brief.get('key_facts', [])[:6]:
                fact_text = f.get('fact', f) if isinstance(f, dict) else str(f)
                source = f.get('source', '') if isinstance(f, dict) else ''
                research_context += f"  · {fact_text}"
                if source:
                    research_context += f" [来源: {source}]"
                research_context += '\n'

            data_points = brief.get('data_points', [])
            if data_points:
                research_context += '\n数据引用：\n'
                for d in data_points[:4]:
                    data_text = d.get('data', d) if isinstance(d, dict) else str(d)
                    source = d.get('source', '') if isinstance(d, dict) else ''
                    research_context += f"  · {data_text}"
                    if source:
                        research_context += f" [来源: {source}]"
                    research_context += '\n'

            perspectives = brief.get('different_perspectives', [])
            if perspectives:
                research_context += '\n多元观点（请在文章中体现）：\n'
                for p in perspectives[:3]:
                    p_text = p.get('perspective', p) if isinstance(p, dict) else str(p)
                    research_context += f"  · {p_text}\n"

            credibility = brief.get('credibility_assessment', '')
            if credibility:
                research_context += f"\n来源可信度评估：{credibility}\n"

        # 附加可引用 URL 列表
        if research.get('results'):
            refs = []
            for i, item in enumerate(research['results'][:5]):
                refs.append(f"[{i+1}] {item.get('title', '')} - {item.get('url', '')}")
            if refs:
                research_context += "\n\n可引用资料链接：\n" + "\n".join(refs)

    system_prompt = (
        '你是一位资深微信公众号主笔，请严格按照大纲和研究简报中的真实信息撰写完整文章。\n'
        + instruction + '\n'
        '核心原则 — Garbage In, Garbage Out：\n'
        '1. 只使用研究简报中明确提供的事实、数据和观点，绝不凭空编造\n'
        '2. 严格按照大纲的章节结构和要点展开，不可遗漏章节\n'
        '3. 正文需连贯流畅，章节间有自然过渡\n'
        '4. 引用数据时注明来源（如"据XX报道""XX数据显示"），增强可信度\n'
        '5. 对于研究简报中没有的信息，使用"目前尚无公开数据""有待进一步确认"等表述\n'
        '6. 呈现多元观点，不偏袒任何一方\n'
        '7. 使用 **加粗**、emoji 等排版元素增强可读性\n'
        '8. 不要重复输出"文章标题："等提示语，直接输出正文内容\n'
        f'正文总字数约 {max_length} 字。'
    )

    user_prompt = (
        f"文章大纲：\n{outline_text}{research_context}\n\n"
        f"请基于以上大纲和其中引用的研究简报信息撰写完整正文。"
        f"写作风格：{style_name}。重要：只使用研究简报中明确提到的事实和数据，不要编造。"
        f"直接输出正文内容，从导语开始，无需再输出标题。"
    )

    full_text = ''
    thinking_received = False

    for sse_line in _chat_stream([
        {'role': 'system', 'content': system_prompt},
        {'role': 'user', 'content': user_prompt},
    ], thinking_enabled=cfg.get('thinking_enabled', False)):
        if sse_line.startswith('data: '):
            data_str = sse_line[6:]
            if data_str == '[DONE]':
                yield {'type': 'done', 'article': {'title': title, 'content': full_text.strip()}}
                return
            try:
                chunk = json.loads(data_str)
                if 'error' in chunk:
                    yield {'type': 'error', 'message': chunk['error']}
                    return
                chunk_type = chunk.get('type', 'content')
                chunk_content = chunk.get('content', '')

                if chunk_type == 'thinking' and chunk_content:
                    if not thinking_received:
                        thinking_received = True
                        yield {'type': 'thinking', 'message': '模型正在思考...'}
                    continue

                if chunk_type == 'content' and chunk_content:
                    full_text += chunk_content
                    yield {'type': 'chunk', 'content': chunk_content}
            except Exception:
                continue

    # fallback: 流未正常结束
    yield {'type': 'done', 'article': {'title': title, 'content': full_text.strip()}}


# ---- 步骤6：检验输出 ----
_VERIFY_SYSTEM = (
    '你是一位资深公众号文章质检编辑。请对生成的文章进行质量检验，输出 JSON 格式：\n'
    '{\n'
    '  "completeness": 0-100,  // 完整度评分\n'
    '  "quality_score": 0-100,  // 整体质量评分\n'
    '  "issues": ["问题1", "问题2"],  // 发现的问题（可能为空数组）\n'
    '  "suggestions": ["改进建议1", "改进建议2"],  // 改进建议\n'
    '  "word_count": 0,  // 实际字数估算\n'
    '  "verdict": "通过/需修订"  // 总体结论\n'
    '}\n'
    '检验维度：结构完整性、逻辑连贯性、内容深度、语言流畅度、引用准确性。只输出 JSON。'
)


def verify_article(title, content, outline=None):
    """步骤6：LLM 自检文章质量。返回 (ok, dict or error_message)"""
    outline_hint = ''
    if outline and outline.get('sections'):
        sections = [s.get('heading', '') for s in outline['sections']]
        outline_hint = f"\n预期章节结构：{', '.join(sections)}"

    user_prompt = (
        f"文章标题：{title}\n\n"
        f"文章正文：\n{content}\n{outline_hint}\n\n"
        f"请对以上文章进行质量检验。"
    )
    ok, content_resp = _chat([
        {'role': 'system', 'content': _VERIFY_SYSTEM},
        {'role': 'user', 'content': user_prompt},
    ], expect_json=True, temperature=0.3, thinking_enabled=False)

    if not ok:
        return False, content_resp
    parsed = _safe_parse_json(content_resp)
    if parsed is None:
        return False, '检验结果无法解析为 JSON'
    parsed.setdefault('completeness', 0)
    parsed.setdefault('quality_score', 0)
    parsed.setdefault('issues', [])
    parsed.setdefault('suggestions', [])
    parsed.setdefault('word_count', len(content))
    parsed.setdefault('verdict', '通过')
    return True, parsed
