"""
TrendArticle · 热点公众号文章生成器 v3.0
==========================================

架构：
- config.py     : 大模型配置管理（环境变量 / 落盘）
- fetchers.py   : 多源热点真实抓取（14个数据源）
- llm_client.py : OpenAI 兼容大模型客户端
- app.py        : Flask 路由与静态托管
- scheduler.py  : 定时后台刷新（可选）
"""
import json
import hashlib
import os
import re
import time
import logging
from collections import defaultdict
from datetime import datetime
from functools import wraps
from urllib.parse import quote
from flask import Flask, jsonify, request, send_from_directory, Response

import fetchers
import llm_client
import search_client
import workflow_engine
import scoring_engine
import digest_engine
from config import get_config, update_config, is_configured

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
app = Flask(__name__, static_folder=BASE_DIR + '/frontend', static_url_path='')
app.config['JSON_AS_ASCII'] = False

# ============================================================
# Hotspot cache with TTL (v4.0: 5分钟过期，自动刷新)
# ============================================================
_HOTSPOT_CACHE = None
_CACHE_TTL = 300  # 5分钟过期（秒）
_LAST_FETCH_TIME = 0  # 记录上次抓取时间戳
_RETRY_COUNT = 0  # 连续失败重试计数
MAX_RETRY_COUNT = 2  # 最多自动重试2次

# Content styles
CONTENT_STYLES = ['professional_depth', 'humorous', 'suspenseful', 'emotional']
CONTENT_STYLE_NAMES = {
    'professional_depth': '专业深度',
    'humorous': '幽默风趣',
    'suspenseful': '悬念吸引',
    'emotional': '情感共鸣',
}


# ============================================================
# Rate limiting
# ============================================================
_rate_limit_store = defaultdict(list)
_RATE_LIMIT_WINDOW = 60
_RATE_LIMIT_MAX = 60

from threading import Lock
_lock = Lock()


def _clean_old_entries(timestamps, window):
    cutoff = time.time() - window
    return [t for t in timestamps if t > cutoff]


def rate_limit(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        client_ip = request.remote_addr or 'unknown'
        key = f"{client_ip}:{f.__name__}"
        with _lock:
            _rate_limit_store[key] = _clean_old_entries(_rate_limit_store[key], _RATE_LIMIT_WINDOW)
            if len(_rate_limit_store[key]) >= _RATE_LIMIT_MAX:
                return jsonify({'error': '请求过于频繁，请稍后再试'}), 429
            _rate_limit_store[key].append(time.time())
        return f(*args, **kwargs)
    return wrapper


def _assign_style(title):
    h = int(hashlib.md5(title.encode('utf-8')).hexdigest(), 16)
    return CONTENT_STYLES[h % len(CONTENT_STYLES)]


def _is_cache_expired():
    """检查缓存是否过期（v4.0: TTL机制，5分钟）"""
    global _LAST_FETCH_TIME
    if _HOTSPOT_CACHE is None:
        return True
    elapsed = time.time() - _LAST_FETCH_TIME
    return elapsed > _CACHE_TTL


def _reset_cache():
    """重置缓存（v4.0: 用于自动重试后）"""
    global _HOTSPOT_CACHE, _LAST_FETCH_TIME, _RETRY_COUNT
    _HOTSPOT_CACHE = None
    _LAST_FETCH_TIME = 0
    _RETRY_COUNT = 0


def _fetch_with_retry():
    """带自动重试的抓取（v4.0: 优雅降级）"""
    global _RETRY_COUNT
    results = fetchers.fetch_all()

    # 统计有效数据源数量
    total_sources = len(fetchers.FETCHERS)
    active_sources = sum(1 for key, items in results.items() if len(items) > 0)

    # 如果有效数据源低于50%，自动重试一次
    if active_sources < total_sources * 0.5 and _RETRY_COUNT < MAX_RETRY_COUNT:
        _RETRY_COUNT += 1
        logger.warning(f"数据源活跃率过低 ({active_sources}/{total_sources})，第{_RETRY_COUNT}次重试...")
        time.sleep(0.5)  # 短暂等待后重试
        return _fetch_with_retry()

    _RETRY_COUNT = 0
    return results


# ============================================================
# Routes — Static files
# ============================================================
@app.route('/')
def index():
    # 主页改为侧边栏统一壳页面
    return send_from_directory(app.static_folder, 'index.html')


@app.route('/workflow')
def workflow_page():
    # 工作流页面（iframe 内使用）
    return send_from_directory(app.static_folder, 'workflow.html')


@app.route('/library')
def library_page():
    # 素材库页面（原热点列表）
    return send_from_directory(app.static_folder, 'library.html')


@app.route('/settings')
def settings_page():
    return send_from_directory(app.static_folder, 'settings.html')


@app.route('/dashboard')
def dashboard_page():
    """商业版仪表盘：日报 + 精选热点 + 信源健康"""
    return send_from_directory(app.static_folder, 'dashboard.html')


@app.route('/style.css')
def css_static():
    return send_from_directory(app.static_folder, 'style.css')


@app.route('/app.js')
def js_static():
    return send_from_directory(app.static_folder, 'app.js')


@app.route('/workflow.html')
def workflow_html():
    return send_from_directory(app.static_folder, 'workflow.html')


@app.route('/workflow.js')
def workflow_js():
    return send_from_directory(app.static_folder, 'workflow.js')


@app.route('/workflow.css')
def workflow_css():
    return send_from_directory(app.static_folder, 'workflow.css')


@app.route('/library.html')
def library_html():
    return send_from_directory(app.static_folder, 'library.html')


@app.route('/library.js')
def library_js():
    return send_from_directory(app.static_folder, 'library.js')


@app.route('/settings.js')
def settings_js():
    return send_from_directory(app.static_folder, 'settings.js')


@app.route('/settings.css')
def settings_css():
    return send_from_directory(app.static_folder, 'settings.css')


@app.route('/dashboard.html')
def dashboard_html():
    return send_from_directory(app.static_folder, 'dashboard.html')


@app.route('/dashboard.js')
def dashboard_js():
    return send_from_directory(app.static_folder, 'dashboard.js')


# ============================================================
# Routes — API
# ============================================================
@app.route('/api/hotspots')
@rate_limit
def api_hotspots():
    """聚合多源真实热点数据，按热度排序返回前 50 条。（v4.0: 带TTL缓存和自动重试）"""
    global _HOTSPOT_CACHE, _LAST_FETCH_TIME
    import time as _time

    force_refresh = request.args.get('refresh', '').lower() in ('true', '1', 'yes')
    source_filter = request.args.get('source', '').strip()
    style_filter = request.args.get('style', '').strip()
    category_filter = request.args.get('category', '').strip()

    if not force_refresh and not _is_cache_expired():
        all_items = _HOTSPOT_CACHE['hotspots']
    else:
        fetch_start = _time.time()
        results = _fetch_with_retry()
        fetch_elapsed = _time.time() - fetch_start

        # 记录最后一次抓取时间
        api_performance._last_fetch_time = fetch_elapsed

        # 错误日志记录
        total_sources = len(fetchers.FETCHERS)
        active_sources = sum(1 for key, items in results.items() if len(items) > 0)
        failed_sources = [k for k, v in results.items() if len(v) == 0]
        if failed_sources:
            logger.warning(f"Fetch completed in {fetch_elapsed:.2f}s: {active_sources}/{total_sources} sources active. Failed: {failed_sources}")

        all_items = []
        sources_count = {}
        for key, items in results.items():
            sources_count[key] = len(items)
            for item in items:
                item['style'] = _assign_style(item.get('title', ''))
                item['source_key'] = key
            all_items.extend(items)

        # Deduplicate by slug
        seen_slugs = set()
        deduped = []
        for item in all_items:
            s = item.get('slug', item.get('title', ''))
            if s not in seen_slugs:
                seen_slugs.add(s)
                deduped.append(item)
        all_items = deduped

        all_items.sort(key=lambda x: x.get('engagement', 0), reverse=True)

        with _lock:
            _HOTSPOT_CACHE = {
                'hotspots': all_items[:50],
                'sources': sources_count,
                'generated_at': datetime.now().isoformat(),
                'fetch_time_sec': round(fetch_elapsed, 2),
            }
            _LAST_FETCH_TIME = _time.time()

    filtered = _HOTSPOT_CACHE['hotspots']

    if source_filter:
        filtered = [h for h in filtered if h.get('source') == source_filter]
    if style_filter:
        filtered = [h for h in filtered if h.get('style') == style_filter]
    if category_filter:
        filtered = [h for h in filtered if h.get('category') == category_filter]

    health_report = fetchers.get_health_report()
    return jsonify({
        'hotspots': filtered,
        'sources': _HOTSPOT_CACHE.get('sources', {}),
        'source_health': health_report,
        'llm_configured': is_configured(),
        'generated_at': _HOTSPOT_CACHE.get('generated_at', datetime.now().isoformat()),
        'cached': not force_refresh and not _is_cache_expired(),
        'style_names': CONTENT_STYLE_NAMES,
        'total_available': len(_HOTSPOT_CACHE['hotspots']),
        'fetch_time_sec': _HOTSPOT_CACHE.get('fetch_time_sec', 0),
        'source_tiers': fetchers.SOURCE_TIERS,  # v6.1: 信源分级信息
        'source_tier_weights': fetchers.SOURCE_TIER_WEIGHTS,
        'source_tier_labels': fetchers.SOURCE_TIER_LABELS,
    })


@app.route('/api/hotspots/scored')
@rate_limit
def api_hotspots_scored():
    """v6.1: 获取经过多维评分 + 精选的热点列表（含信源分级）"""
    global _HOTSPOT_CACHE
    force_refresh = request.args.get('refresh', '').lower() in ('true', '1', 'yes')

    if not force_refresh and not _is_cache_expired():
        all_items = _HOTSPOT_CACHE['hotspots']
    else:
        # 触发刷新
        api_hotspots()
        all_items = _HOTSPOT_CACHE['hotspots']

    # 确保 source_key 存在
    for item in all_items:
        if 'source_key' not in item:
            item['source_key'] = item.get('source_key', '')

    # 分类筛选
    category_filter = request.args.get('category', '').strip()
    tier_filter = request.args.get('tier', '').strip()
    limit = request.args.get('limit', 30, type=int)

    if category_filter:
        all_items = [h for h in all_items if h.get('category') == category_filter]
    if tier_filter:
        all_items = [h for h in all_items 
                     if fetchers.SOURCE_TIERS.get(h.get('source_key', '')) == tier_filter]

    # 使用注册的评分引擎进行批量评分
    def quick_score(hotspot):
        """快速评分：基于信源分级 + 热度值，不调用 LLM（性能优先）"""
        source_key = hotspot.get('source_key', '')
        tier_info = fetchers.get_source_tier_info(source_key)
        engagement = hotspot.get('engagement', 0)
        
        # 规范化 engagement 到 0-100
        max_eng = 1000000
        eng_score = min(100, int(engagement / max_eng * 100)) if max_eng > 0 else 50
        
        # 基于信源权重估算各维度分（无 LLM 时的 fallback）
        base = min(80, 40 + tier_info['weight'] * 15)
        scores = {
            'freshness': min(100, base - 10),
            'authority': min(100, 50 + tier_info['weight'] * 20),
            'relevance': min(100, base + 5),
            'social_impact': min(100, eng_score),
            'uniqueness': min(100, 50 - tier_info['weight'] * 5),
            'reason': f'快速评分（{tier_info["label"]}，热度 {engagement}）'
        }
        
        quality = scoring_engine.calculate_quality_score(scores, source_key)
        
        hotspot['scoring'] = quality
        hotspot['bucket'] = scoring_engine.classify_to_bucket(
            hotspot.get('title', ''), hotspot.get('desc', ''), hotspot.get('source', '')
        )
        hotspot['bucket_name'] = scoring_engine.CATEGORY_BUCKETS.get(
            hotspot['bucket'], {}).get('name', '社会热点')
        hotspot['bucket_icon'] = scoring_engine.CATEGORY_BUCKETS.get(
            hotspot['bucket'], {}).get('icon', '🔥')
        hotspot['source_tier'] = tier_info['tier']
        hotspot['source_tier_label'] = tier_info['label']
        return hotspot

    # 批量评分
    scored = [quick_score(h) for h in all_items]
    
    # 精选排序
    scored.sort(key=lambda x: (
        not x['scoring']['is_selected'],
        -x['scoring']['final_score']
    ))
    
    # 统计
    stats = scoring_engine.get_scoring_stats(scored)
    
    return jsonify({
        'hotspots': scored[:limit],
        'stats': stats,
        'categories': list(scoring_engine.CATEGORY_BUCKETS.keys()),
        'category_names': {k: v['name'] for k, v in scoring_engine.CATEGORY_BUCKETS.items()},
        'category_icons': {k: v['icon'] for k, v in scoring_engine.CATEGORY_BUCKETS.items()},
        'cached': not force_refresh,
    })


@app.route('/api/digest/latest')
@rate_limit
def api_digest_latest():
    """v6.1: 获取最新日报"""
    digest = digest_engine.get_latest_digest()
    if digest is None:
        return jsonify({'error': '暂无日报，请先生成'}), 404
    return jsonify({'digest': digest})


@app.route('/api/digest/generate', methods=['POST'])
@rate_limit
def api_digest_generate():
    """v6.1: 生成日报（基于当前缓存的热点数据）"""
    global _HOTSPOT_CACHE
    if _HOTSPOT_CACHE is None or not _HOTSPOT_CACHE.get('hotspots'):
        return jsonify({'error': '暂无热点数据，请先加载热点'}), 400

    date_str = (request.json or {}).get('date', None)
    hotspots = _HOTSPOT_CACHE['hotspots']

    # 快速评分
    for item in hotspots:
        if 'source_key' not in item:
            item['source_key'] = ''
        source_key = item.get('source_key', '')
        tier_info = fetchers.get_source_tier_info(source_key)
        engagement = item.get('engagement', 0)
        eng_score = min(100, int(engagement / 10000)) if engagement else 50
        base = min(80, 40 + tier_info['weight'] * 15)
        scores = {
            'freshness': min(100, base - 10),
            'authority': min(100, 50 + tier_info['weight'] * 20),
            'relevance': min(100, base + 5),
            'social_impact': min(100, eng_score),
            'uniqueness': min(100, 50 - tier_info['weight'] * 5),
            'reason': f'快速评分（{tier_info["label"]}）'
        }
        quality = scoring_engine.calculate_quality_score(scores, source_key)
        item['scoring'] = quality
        item['bucket'] = scoring_engine.classify_to_bucket(
            item.get('title', ''), item.get('desc', ''), item.get('source', '')
        )

    digest = digest_engine.generate_daily_digest(hotspots, date_str)
    return jsonify({'ok': True, 'digest': digest})


@app.route('/api/digest/list')
@rate_limit
def api_digest_list():
    """v6.1: 日报历史列表"""
    limit = request.args.get('limit', 7, type=int)
    digests = digest_engine.list_digests(limit=limit)
    return jsonify({'digests': digests})


@app.route('/api/digest/<date_str>')
@rate_limit
def api_digest_by_date(date_str):
    """v6.1: 获取指定日期的日报"""
    digest = digest_engine.get_digest_by_date(date_str)
    if digest is None:
        return jsonify({'error': f'未找到 {date_str} 的日报'}), 404
    return jsonify({'digest': digest})


@app.route('/api/digest/<date_str>/export', methods=['POST'])
@rate_limit
def api_digest_export(date_str):
    """v6.1: 导出日报为 Markdown"""
    digest = digest_engine.get_digest_by_date(date_str)
    if digest is None:
        return jsonify({'error': f'未找到 {date_str} 的日报'}), 404
    
    md = digest_engine.generate_digest_markdown(digest)
    safe_date = date_str.replace('-', '')
    filename = f"热点日报_{safe_date}.md"
    encoded_filename = quote(filename)
    
    return Response(
        md.encode('utf-8'),
        mimetype='text/markdown',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"},
    )


@app.route('/api/health')
@rate_limit
def api_health():
    """数据源健康状态报告"""
    return jsonify({
        'sources': fetchers.get_health_report(),
        'cache_age': time.time() - _LAST_FETCH_TIME if _HOTSPOT_CACHE else -1,
        'cache_ttl': _CACHE_TTL,
    })


@app.route('/api/generate-tweet', methods=['POST'])
@rate_limit
def generate_tweet():
    """基于热点生成公众号文章"""
    if not is_configured():
        return jsonify({
            'error': '大模型尚未配置，请前往「设置」页面配置 API Key 与 Base URL 后重试。'
        }), 400

    data = request.json
    if data is None:
        return jsonify({'error': '请求格式错误，请发送有效的 JSON'}), 400

    hotspots_data = data.get('hotspots')
    if not hotspots_data:
        return jsonify({'error': '未提供热点数据'}), 400

    hotspot_id = data.get('hotspot_id')
    style = data.get('style', 'professional_depth')

    if style not in llm_client.STYLES:
        style = 'professional_depth'

    hotspot = None
    for h in hotspots_data:
        if h.get('id') == hotspot_id or (not hotspot_id and h.get('title') == data.get('hotspot_title')):
            hotspot = h
            break
    if not hotspot:
        hotspot = hotspots_data[0]

    cfg = get_config()
    if cfg.get('stream_enabled'):
        return _generate_article_stream_response(hotspot, style)

    ok, result = llm_client.generate_article(hotspot, style=style)
    if not ok:
        return jsonify({'error': result}), 502

    return jsonify({
        'style_name': llm_client.STYLES.get(style, {}).get('name', style),
        'hotspot': hotspot.get('title', '未知话题'),
        'model': cfg.get('model'),
        'article': {
            'title': result['title'],
            'content': result['content'],
        },
        'tips': [
            '配图可增加 150% 的阅读量',
            '发布时间建议在早 8 点或晚 8 点',
            '朋友圈转发时配一段引语效果更佳',
            '在文末添加引导关注和互动话术',
        ],
    })


def _generate_article_stream_response(hotspot, style):
    """流式生成公众号文章的 SSE 响应"""
    cfg = get_config()
    article_title = None
    article_content = ''

    def generate():
        nonlocal article_title, article_content

        for event in llm_client.generate_article_stream(hotspot, style=style):
            if event['type'] == 'title':
                article_title = event['content']
                yield f"data: {json.dumps({'type': 'title', 'content': article_title})}\n\n"
            elif event['type'] == 'chunk':
                article_content += event['content']
                yield f"data: {json.dumps({'type': 'chunk', 'content': event['content']})}\n\n"
            elif event['type'] == 'thinking':
                yield f"data: {json.dumps({'type': 'thinking', 'message': event.get('message', '模型正在思考...')})}\n\n"
            elif event['type'] == 'error':
                yield f"data: {json.dumps({'type': 'error', 'message': event['message']})}\n\n"
                return
            elif event['type'] == 'done':
                art = event.get('article', {})
                article_title = art.get('title', article_title or hotspot.get('title', '未知话题'))
                article_content = art.get('content', article_content)
                yield f"data: {json.dumps({'type': 'done', 'article': {'title': article_title, 'content': article_content}, 'style_name': llm_client.STYLES.get(style, {}).get('name', style), 'hotspot': hotspot.get('title', '未知话题'), 'model': cfg.get('model')})}\n\n"
                return

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/analyze-hotspot', methods=['POST'])
@rate_limit
def analyze_hotspot():
    """调用大模型对单个热点做监控分析"""
    if not is_configured():
        return jsonify({
            'error': '大模型尚未配置，请前往「设置」页面配置 API Key 与 Base URL 后重试。'
        }), 400

    data = request.json
    if data is None:
        return jsonify({'error': '请求格式错误'}), 400

    hotspot = data.get('hotspot')
    if not hotspot:
        return jsonify({'error': '未提供热点数据'}), 400

    analysis = llm_client.monitor_hotspot(hotspot)
    return jsonify({'hotspot': hotspot.get('title', ''), 'analysis': analysis})


@app.route('/api/export/markdown', methods=['POST'])
@rate_limit
def export_markdown():
    """导出单篇文章为 Markdown 格式"""
    if not is_configured():
        return jsonify({'error': '大模型尚未配置'}), 400

    data = request.json
    if not data:
        return jsonify({'error': '请求格式错误'}), 400

    hotspot = data.get('hotspot')
    style = data.get('style', 'professional_depth')

    ok, result = llm_client.generate_article(hotspot, style=style)
    if not ok:
        return jsonify({'error': result}), 502

    # Build Markdown
    md = f"# {result['title']}\n\n"
    md += f"> 基于热点：{hotspot.get('title', '')} | 来源：{hotspot.get('source', '')}\n\n"
    md += f"---\n\n"
    md += result['content']
    md += f"\n\n---\n\n"
    md += f"*本文由 TrendArticle 自动生成，数据来源于 {hotspot.get('source', '')}* "
    md += f"| 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}\n"

    return jsonify({
        'markdown': md,
        'title': result['title'],
        'word_count': len(result['content']),
    })


@app.route('/api/export/markdown-file', methods=['POST'])
@rate_limit
def export_markdown_file():
    """导出文章为 Markdown 文件下载（v3.0: 新增文件下载端点）"""
    if not is_configured():
        return jsonify({'error': '大模型尚未配置'}), 400

    data = request.json
    if not data:
        return jsonify({'error': '请求格式错误'}), 400

    hotspot = data.get('hotspot')
    style = data.get('style', 'professional_depth')

    ok, result = llm_client.generate_article(hotspot, style=style)
    if not ok:
        return jsonify({'error': result}), 502

    md = f"# {result['title']}\n\n"
    md += f"> 基于热点：{hotspot.get('title', '')} | 来源：{hotspot.get('source', '')}\n\n"
    md += "---\n\n"
    md += result['content']
    md += f"\n\n---\n\n"
    md += f"*本文由 TrendArticle 自动生成 | 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"

    # 生成安全的文件名
    safe_title = re.sub(r'[^\w一-鿿-]', '_', result['title'][:50])
    filename = f"{safe_title}.md"
    encoded_filename = quote(filename)

    return Response(
        md.encode('utf-8'),
        mimetype='text/markdown',
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}",
            'Content-Length': str(len(md.encode('utf-8'))),
        }
    )


@app.route('/api/export/batch-markdown', methods=['POST'])
@rate_limit
def export_batch_markdown():
    """批量导出多篇文章为 Markdown（v3.0: 新增）"""
    if not is_configured():
        return jsonify({'error': '大模型尚未配置'}), 400

    data = request.json
    if not data or not data.get('articles'):
        return jsonify({'error': '未提供文章数据'}), 400

    articles = data['articles']  # [{hotspot, style, title, content}]
    md_parts = []
    for art in articles:
        md_parts.append(f"# {art.get('title', '无标题')}\n\n")
        md_parts.append(f"> 基于热点：{art.get('hotspot_title', '')} | 来源：{art.get('source', '')}\n\n")
        md_parts.append("---\n\n")
        md_parts.append(art.get('content', ''))
        md_parts.append(f"\n\n---\n\n")

    md = ''.join(md_parts)
    md += f"\n*批量导出时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"

    safe_title = f"批量文章_{datetime.now().strftime('%Y%m%d_%H%M')}"
    filename = f"{safe_title}.md"
    encoded_filename = quote(filename)

    return Response(
        md.encode('utf-8'),
        mimetype='text/markdown',
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}",
        }
    )


@app.route('/api/export/full-article', methods=['POST'])
@rate_limit
def export_full_article():
    """导出多篇热点的综合公众号文章"""
    if not is_configured():
        return jsonify({'error': '大模型尚未配置'}), 400

    data = request.json
    if not data or not data.get('hotspots'):
        return jsonify({'error': '未提供热点数据'}), 400

    hotspots = data['hotspots'][:5]
    style = data.get('style', 'professional_depth')

    cfg = get_config()
    ok, result = llm_client.generate_multi_hotspot_article(hotspots, style=style)
    if not ok:
        return jsonify({'error': result}), 502

    return jsonify({
        'style_name': llm_client.STYLES.get(style, {}).get('name', style),
        'model': cfg.get('model'),
        'article': result,
        'hotspot_count': len(hotspots),
    })


# ============================================================
# 渐进式创作工作流 API
# ============================================================
@app.route('/api/workflow/create', methods=['POST'])
@rate_limit
def workflow_create():
    """创建工作流。body: {source, source_data, config}"""
    if not is_configured():
        return jsonify({'error': '大模型尚未配置，请前往「设置」页面配置 API Key 与 Base URL 后重试。'}), 400

    data = request.json
    if data is None:
        return jsonify({'error': '请求格式错误'}), 400

    source = data.get('source', 'custom')
    source_data = data.get('source_data', {})
    config = data.get('config', {})

    if not source_data:
        return jsonify({'error': '未提供素材数据'}), 400

    wf_id = workflow_engine.create_workflow(source, source_data, config)
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'workflow': wf})


@app.route('/api/workflow/<wf_id>')
@rate_limit
def workflow_get(wf_id):
    """获取工作流完整状态"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404
    return jsonify({'workflow': wf})


@app.route('/api/workflows')
@rate_limit
def workflow_list():
    """列出工作流历史"""
    limit = request.args.get('limit', 20, type=int)
    wfs = workflow_engine.list_workflows(limit=limit)
    return jsonify({'workflows': wfs})


@app.route('/api/workflow/<wf_id>', methods=['DELETE'])
@rate_limit
def workflow_delete(wf_id):
    """删除工作流"""
    workflow_engine.delete_workflow(wf_id)
    return jsonify({'ok': True})


@app.route('/api/workflow/<wf_id>/parse', methods=['POST'])
@rate_limit
def workflow_parse(wf_id):
    """步骤1：LLM 素材解析"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    workflow_engine.update_step(wf_id, 'parse', status=workflow_engine.STEP_STATUS_RUNNING)
    ok, result = llm_client.parse_material(wf['source_data'], wf['source'])
    if not ok:
        workflow_engine.update_step(wf_id, 'parse', status=workflow_engine.STEP_STATUS_FAILED, output={'error': result})
        return jsonify({'error': result}), 502

    sub_tasks = [
        {'name': '提取核心概念', 'status': 'completed'},
        {'name': '分析目标受众', 'status': 'completed'},
        {'name': '识别创作角度', 'status': 'completed'},
        {'name': '生成搜索关键词', 'status': 'completed'},
    ]
    workflow_engine.update_step(wf_id, 'parse', status=workflow_engine.STEP_STATUS_COMPLETED, output=result, sub_tasks=sub_tasks)
    next_step, is_last = workflow_engine.advance_step(wf_id)
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'result': result, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/research', methods=['POST'])
@rate_limit
def workflow_research(wf_id):
    """步骤2：真实搜索调研"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    parse_step = workflow_engine.get_step(wf_id, 'parse')
    parsed_material = (parse_step or {}).get('output') or {}

    workflow_engine.update_step(wf_id, 'research', status=workflow_engine.STEP_STATUS_RUNNING,
                                input_data={'parsed': parsed_material})

    # 步骤2a：生成搜索计划
    ok, plan = llm_client.generate_search_plan(parsed_material)
    if not ok:
        workflow_engine.update_step(wf_id, 'research', status=workflow_engine.STEP_STATUS_FAILED, output={'error': plan})
        return jsonify({'error': plan}), 502

    queries = plan.get('search_queries', [])
    sub_tasks = [{'name': f'生成搜索计划（{len(queries)}个查询）', 'status': 'completed'}]

    # 步骤2b：执行搜索
    all_results = []
    for i, q in enumerate(queries[:5]):
        sub_tasks.append({'name': f'搜索：{q[:20]}', 'status': 'running'})
        workflow_engine.update_step(wf_id, 'research', sub_tasks=sub_tasks)
        results = search_client.search(q, num_results=5)
        for r in results:
            r['query'] = q
        all_results.extend(results)
        sub_tasks[-1]['status'] = 'completed' if results else 'failed'

    # 去重
    seen_urls = set()
    deduped = []
    for r in all_results:
        u = r.get('url', '')
        if u and u not in seen_urls:
            seen_urls.add(u)
            deduped.append(r)

    research_output = {
        'plan': plan,
        'results': deduped[:20],
        'total_found': len(all_results),
    }
    sub_tasks.append({'name': f'汇总资料（{len(deduped)}条）', 'status': 'completed'})
    workflow_engine.update_step(wf_id, 'research', status=workflow_engine.STEP_STATUS_COMPLETED,
                                output=research_output, sub_tasks=sub_tasks)
    next_step, is_last = workflow_engine.advance_step(wf_id)
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'result': research_output, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/topics', methods=['POST'])
@rate_limit
def workflow_topics(wf_id):
    """步骤3：生成候选选题"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    parse_step = workflow_engine.get_step(wf_id, 'parse')
    research_step = workflow_engine.get_step(wf_id, 'research')
    parsed_material = (parse_step or {}).get('output') or {}
    research = (research_step or {}).get('output') or {}

    workflow_engine.update_step(wf_id, 'topics', status=workflow_engine.STEP_STATUS_RUNNING,
                                input_data={'parsed': parsed_material, 'research': research})

    ok, result = llm_client.generate_topics(parsed_material, research)
    if not ok:
        workflow_engine.update_step(wf_id, 'topics', status=workflow_engine.STEP_STATUS_FAILED, output={'error': result})
        return jsonify({'error': result}), 502

    sub_tasks = [
        {'name': '分析调研资料', 'status': 'completed'},
        {'name': f'生成 {len(result.get("topics", []))} 个候选选题', 'status': 'completed'},
        {'name': '等待用户选择', 'status': 'running'},
    ]
    workflow_engine.update_step(wf_id, 'topics', status=workflow_engine.STEP_STATUS_WAITING_USER,
                                output=result, sub_tasks=sub_tasks)
    workflow_engine.set_workflow_status(wf_id, workflow_engine.WF_STATUS_WAITING_USER, current_step='topics')
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'result': result, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/topic/select', methods=['POST'])
@rate_limit
def workflow_topic_select(wf_id):
    """步骤3：用户选择选题"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    data = request.json or {}
    topic = data.get('topic')
    if not topic:
        return jsonify({'error': '未提供选题'}), 400

    topics_step = workflow_engine.get_step(wf_id, 'topics')
    output = (topics_step or {}).get('output') or {}
    output['selected_topic'] = topic
    sub_tasks = [
        {'name': '分析调研资料', 'status': 'completed'},
        {'name': '生成候选选题', 'status': 'completed'},
        {'name': f'已选择：{topic.get("title", "")[:20]}', 'status': 'completed'},
    ]
    workflow_engine.update_step(wf_id, 'topics', status=workflow_engine.STEP_STATUS_COMPLETED,
                                output=output, sub_tasks=sub_tasks)
    next_step, is_last = workflow_engine.advance_step(wf_id)
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/outline', methods=['POST'])
@rate_limit
def workflow_outline(wf_id):
    """步骤4：生成大纲"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    topics_step = workflow_engine.get_step(wf_id, 'topics')
    topics_output = (topics_step or {}).get('output') or {}
    topic = topics_output.get('selected_topic') or (topics_output.get('topics') or [{}])[0]

    parse_step = workflow_engine.get_step(wf_id, 'parse')
    research_step = workflow_engine.get_step(wf_id, 'research')
    parsed_material = (parse_step or {}).get('output') or {}
    research = (research_step or {}).get('output') or {}

    style = wf['config'].get('style', 'professional_depth')

    workflow_engine.update_step(wf_id, 'outline', status=workflow_engine.STEP_STATUS_RUNNING,
                                input_data={'topic': topic})

    ok, result = llm_client.generate_outline(topic, parsed_material, research, style=style)
    if not ok:
        workflow_engine.update_step(wf_id, 'outline', status=workflow_engine.STEP_STATUS_FAILED, output={'error': result})
        return jsonify({'error': result}), 502

    sub_tasks = [
        {'name': '设计文章结构', 'status': 'completed'},
        {'name': f'生成 {len(result.get("sections", []))} 个章节大纲', 'status': 'completed'},
        {'name': '等待用户确认', 'status': 'running'},
    ]
    workflow_engine.update_step(wf_id, 'outline', status=workflow_engine.STEP_STATUS_WAITING_USER,
                                output=result, sub_tasks=sub_tasks)
    workflow_engine.set_workflow_status(wf_id, workflow_engine.WF_STATUS_WAITING_USER, current_step='outline')
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'result': result, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/outline/confirm', methods=['POST'])
@rate_limit
def workflow_outline_confirm(wf_id):
    """步骤4：用户确认/编辑大纲"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    data = request.json or {}
    outline = data.get('outline')

    if outline:
        # 用户编辑了大纲
        workflow_engine.update_step(wf_id, 'outline', output=outline)

    sub_tasks = [
        {'name': '设计文章结构', 'status': 'completed'},
        {'name': '生成章节大纲', 'status': 'completed'},
        {'name': '用户已确认大纲', 'status': 'completed'},
    ]
    workflow_engine.update_step(wf_id, 'outline', status=workflow_engine.STEP_STATUS_COMPLETED, sub_tasks=sub_tasks)
    next_step, is_last = workflow_engine.advance_step(wf_id)
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/generate', methods=['POST'])
@rate_limit
def workflow_generate(wf_id):
    """步骤5：流式生成正文（SSE）"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    outline_step = workflow_engine.get_step(wf_id, 'outline')
    outline = (outline_step or {}).get('output') or {}
    research_step = workflow_engine.get_step(wf_id, 'research')
    research = (research_step or {}).get('output') or {}
    style = wf['config'].get('style', 'professional_depth')
    max_length = wf['config'].get('article_max_length')

    workflow_engine.update_step(wf_id, 'generate', status=workflow_engine.STEP_STATUS_RUNNING,
                                input_data={'outline': outline})
    workflow_engine.set_workflow_status(wf_id, workflow_engine.WF_STATUS_RUNNING, current_step='generate')

    def generate():
        article_title = outline.get('title', '未命名文章')
        article_content = ''

        try:
            for event in llm_client.generate_body_stream(outline, style=style, research=research, max_length=max_length):
                if event['type'] == 'title':
                    article_title = event['content']
                    yield f"data: {json.dumps({'type': 'title', 'content': article_title})}\n\n"
                elif event['type'] == 'chunk':
                    article_content += event['content']
                    yield f"data: {json.dumps({'type': 'chunk', 'content': event['content']})}\n\n"
                elif event['type'] == 'thinking':
                    yield f"data: {json.dumps({'type': 'thinking', 'message': event.get('message', '模型正在思考...')})}\n\n"
                elif event['type'] == 'error':
                    workflow_engine.update_step(wf_id, 'generate',
                                               status=workflow_engine.STEP_STATUS_FAILED,
                                               output={'error': event['message']})
                    workflow_engine.set_workflow_status(wf_id, workflow_engine.WF_STATUS_FAILED, current_step='generate')
                    yield f"data: {json.dumps({'type': 'error', 'message': event['message']})}\n\n"
                    return
                elif event['type'] == 'done':
                    art = event.get('article', {})
                    article_title = art.get('title', article_title)
                    article_content = art.get('content', article_content)
                    # 保存文章到工作流
                    workflow_engine.save_article(wf_id, article_title, article_content)
                    workflow_engine.update_step(wf_id, 'generate',
                                               status=workflow_engine.STEP_STATUS_COMPLETED,
                                               output={'title': article_title, 'word_count': len(article_content)},
                                               sub_tasks=[
                                                   {'name': '生成导语', 'status': 'completed'},
                                                   {'name': '撰写正文段落', 'status': 'completed'},
                                                   {'name': '收尾结语', 'status': 'completed'},
                                               ])
                    workflow_engine.advance_step(wf_id)
                    yield f"data: {json.dumps({'type': 'done', 'article': {'title': article_title, 'content': article_content}})}\n\n"
                    return
        except Exception as e:
            err_msg = f'正文生成异常：{str(e)}'
            logger.error(f'[workflow_generate] {err_msg}')
            workflow_engine.update_step(wf_id, 'generate',
                                       status=workflow_engine.STEP_STATUS_FAILED,
                                       output={'error': err_msg})
            workflow_engine.set_workflow_status(wf_id, workflow_engine.WF_STATUS_FAILED, current_step='generate')
            yield f"data: {json.dumps({'type': 'error', 'message': err_msg})}\n\n"
            return

    return Response(generate(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/api/workflow/<wf_id>/reset/<step_name>', methods=['POST'])
@rate_limit
def workflow_reset(wf_id, step_name):
    """将工作流重置到指定步骤（允许回到前置步骤重新选择）"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    ok, msg = workflow_engine.reset_to_step(wf_id, step_name)
    if not ok:
        return jsonify({'error': msg}), 400

    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'message': msg, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/verify', methods=['POST'])
@rate_limit
def workflow_verify(wf_id):
    """步骤6：LLM 检验输出"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    title = wf.get('article_title') or ''
    content = wf.get('article_content') or ''
    if not content:
        return jsonify({'error': '文章尚未生成'}), 400

    outline_step = workflow_engine.get_step(wf_id, 'outline')
    outline = (outline_step or {}).get('output') or {}

    workflow_engine.update_step(wf_id, 'verify', status=workflow_engine.STEP_STATUS_RUNNING,
                                input_data={'title': title})

    ok, result = llm_client.verify_article(title, content, outline=outline)
    if not ok:
        workflow_engine.update_step(wf_id, 'verify', status=workflow_engine.STEP_STATUS_FAILED, output={'error': result})
        return jsonify({'error': result}), 502

    sub_tasks = [
        {'name': '检查结构完整性', 'status': 'completed'},
        {'name': '评估逻辑连贯性', 'status': 'completed'},
        {'name': '校验内容深度', 'status': 'completed'},
        {'name': '等待用户交付', 'status': 'running'},
    ]
    workflow_engine.update_step(wf_id, 'verify', status=workflow_engine.STEP_STATUS_WAITING_USER,
                                output=result, sub_tasks=sub_tasks)
    workflow_engine.set_workflow_status(wf_id, workflow_engine.WF_STATUS_WAITING_USER, current_step='verify')
    wf = workflow_engine.get_workflow(wf_id)
    return jsonify({'ok': True, 'result': result, 'workflow': wf})


@app.route('/api/workflow/<wf_id>/export', methods=['POST'])
@rate_limit
def workflow_export(wf_id):
    """导出工作流文章为 Markdown 文件"""
    wf = workflow_engine.get_workflow(wf_id)
    if wf is None:
        return jsonify({'error': '工作流不存在'}), 404

    title = wf.get('article_title') or '未命名文章'
    content = wf.get('article_content') or ''
    if not content:
        return jsonify({'error': '文章尚未生成'}), 400

    # 标记交付完成
    sub_tasks = [
        {'name': '检查结构完整性', 'status': 'completed'},
        {'name': '评估逻辑连贯性', 'status': 'completed'},
        {'name': '校验内容深度', 'status': 'completed'},
        {'name': '用户已交付', 'status': 'completed'},
    ]
    workflow_engine.update_step(wf_id, 'verify', status=workflow_engine.STEP_STATUS_COMPLETED, sub_tasks=sub_tasks)
    workflow_engine.set_workflow_status(wf_id, workflow_engine.WF_STATUS_COMPLETED, current_step='verify')

    md = f"# {title}\n\n"
    md += f"> 由 TrendArticle 工作流生成\n\n---\n\n"
    md += content
    md += f"\n\n---\n\n*生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}*\n"

    safe_title = re.sub(r'[^\w一-鿿\-]', '_', title[:50])
    filename = f"{safe_title}.md"
    # RFC 5987: 非 ASCII 文件名必须用 filename*=UTF-8''{url_encoded} 编码
    encoded_filename = quote(filename)

    return Response(
        md.encode('utf-8'),
        mimetype='text/markdown',
        headers={'Content-Disposition': f"attachment; filename*=UTF-8''{encoded_filename}"},
    )



@app.route('/api/llm-config', methods=['GET'])
def get_llm_config():
    cfg = get_config()
    return jsonify({
        'api_key_set': bool(cfg.get('api_key')),
        'api_key_masked': ('****' + cfg['api_key'][-4:]) if cfg.get('api_key') else '',
        'base_url': cfg.get('base_url', ''),
        'model': cfg.get('model', ''),
        'temperature': cfg.get('temperature', 0.8),
        'stream_enabled': cfg.get('stream_enabled', False),
        'thinking_enabled': cfg.get('thinking_enabled', False),
        'thinking_budget_tokens': cfg.get('thinking_budget_tokens', 2048),
        'article_max_length': cfg.get('article_max_length', 2000),
        'proxy': cfg.get('proxy', ''),
        'search_provider': cfg.get('search_provider', 'duckduckgo'),
        'search_api_key_set': bool(cfg.get('search_api_key')),
        'search_base_url': cfg.get('search_base_url', ''),
        'configured': is_configured(),
    })


@app.route('/api/llm-config', methods=['POST'])
@rate_limit
def post_llm_config():
    data = request.json
    if data is None:
        return jsonify({'error': '请求格式错误'}), 400
    cfg = update_config(data)
    return jsonify({
        'ok': True,
        'configured': is_configured(),
        'base_url': cfg.get('base_url'),
        'model': cfg.get('model'),
    })


@app.route('/api/test-llm', methods=['POST'])
@rate_limit
def test_llm():
    ok, msg = llm_client.test_connection()
    return jsonify({'ok': ok, 'message': msg})


# ============================================================
# 数据源健康检查与性能监控（v5.0）
# ============================================================
@app.route('/api/health-summary')
@rate_limit
def api_health_summary():
    """数据源健康状态摘要"""
    summary = fetchers.get_source_status_summary()
    return jsonify(summary)


@app.route('/api/performance')
@rate_limit
def api_performance():
    """系统性能监控面板"""
    import psutil
    import os

    try:
        mem = psutil.virtual_memory()
        cpu_percent = psutil.cpu_percent(interval=0.1)
        process = psutil.Process(os.getpid())
        mem_info = process.memory_info()
        fetch_time = getattr(api_performance, '_last_fetch_time', 0)
    except Exception:
        mem = None
        cpu_percent = 0
        mem_info = None
        fetch_time = 0

    return jsonify({
        'cpu_percent': round(cpu_percent, 1),
        'memory_total_mb': round(mem.total / 1024 / 1024, 1) if mem else 0,
        'memory_used_mb': round(mem.used / 1024 / 1024, 1) if mem else 0,
        'memory_percent': round(mem.percent, 1) if mem else 0,
        'process_rss_mb': round(mem_info.rss / 1024 / 1024, 1) if mem_info else 0,
        'process_vms_mb': round(mem_info.vms / 1024 / 1024, 1) if mem_info else 0,
        'last_fetch_time_sec': round(fetch_time, 2),
        'cache_ttl': _CACHE_TTL,
        'cache_age': time.time() - _LAST_FETCH_TIME if _HOTSPOT_CACHE else -1,
        'uptime': time.time() - api_performance._start_time if hasattr(api_performance, '_start_time') else 0,
    })


# ============================================================
# 数据源手动切换（v5.0）
# ============================================================
@app.route('/api/source/switch', methods=['POST'])
@rate_limit
def api_switch_source():
    """手动切换数据源配置（如切换 B站 的 popular/ranking 模式）"""
    data = request.json
    if not data:
        return jsonify({'error': '请求格式错误'}), 400

    source_key = data.get('source_key', '')
    mode = data.get('mode', '')

    valid_modes = {
        'bilibili': ['popular', 'ranking'],
    }

    if source_key not in valid_modes:
        return jsonify({'error': f'不支持的数据源: {source_key}'}), 400

    if mode not in valid_modes[source_key]:
        return jsonify({'error': f'无效的模式: {mode}，可选: {valid_modes[source_key]}'}), 400

    # 存储用户偏好
    if not hasattr(app, 'source_preferences'):
        app.source_preferences = {}
    app.source_preferences[source_key] = mode

    return jsonify({
        'ok': True,
        'source': source_key,
        'mode': mode,
        'message': f'已切换到 {mode} 模式',
    })


@app.route('/api/source/preferences')
@rate_limit
def api_source_preferences():
    """获取数据源偏好配置"""
    prefs = getattr(app, 'source_preferences', {})
    available_modes = {
        'bilibili': ['popular', 'ranking'],
    }
    result = {}
    for key, modes in available_modes.items():
        result[key] = {
            'modes': modes,
            'current': prefs.get(key, modes[0]),
        }
    return jsonify(result)


# ============================================================
# 文章历史 API
# ============================================================

@app.route('/api/articles', methods=['GET'])
@rate_limit
def api_get_articles():
    """获取已保存的文章列表"""
    limit = request.args.get('limit', 50, type=int)
    offset = request.args.get('offset', 0, type=int)
    articles = workflow_engine.get_saved_articles(limit=limit, offset=offset)
    return jsonify({'articles': articles, 'total': len(articles)})


@app.route('/api/articles/<aid>', methods=['GET'])
@rate_limit
def api_get_article(aid):
    """获取单篇文章完整内容"""
    article = workflow_engine.get_saved_article(aid)
    if not article:
        return jsonify({'error': '文章不存在'}), 404
    return jsonify({'article': article})


@app.route('/api/articles', methods=['POST'])
@rate_limit
def api_save_article():
    """保存一篇文章到历史"""
    data = request.json
    if not data or not data.get('content'):
        return jsonify({'error': '缺少文章内容'}), 400
    aid = workflow_engine.save_article_history(
        title=data.get('title', '无标题'),
        content=data.get('content', ''),
        style=data.get('style', ''),
        source=data.get('source', ''),
        hotspot_title=data.get('hotspot_title', ''),
        word_count=data.get('word_count', 0),
    )
    return jsonify({'ok': True, 'article_id': aid})


@app.route('/api/articles/<aid>', methods=['DELETE'])
@rate_limit
def api_delete_article(aid):
    """删除一篇已保存的文章"""
    workflow_engine.delete_saved_article(aid)
    return jsonify({'ok': True})


# ============================================================
# 系统启动时间记录
# ============================================================
api_performance._start_time = time.time()
api_performance._last_fetch_time = 0


if __name__ == '__main__':
    print("🚀 TrendArticle v3.0 — 热点公众号文章生成器")
    print("📱 访问 http://localhost:5000")
    print("⚙️  设置页: http://localhost:5000/settings")
    app.run(host='0.0.0.0', port=5000, debug=False)
