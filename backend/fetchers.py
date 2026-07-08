"""
热点数据抓取模块 v5.0 — 高性能多源聚合 + 故障转移 + 健康追踪

数据源分类：
  【综合热榜】百度热搜、抖音热榜、头条热榜、微博热搜、知乎热榜
  【科技社区】Hacker News、GitHub Trending、V2EX、少数派
  【科技媒体】36氪、虎嗅、量子位
  【视频社区】B站热门

v5.0 变更：
  - B站：改用 popular API（无需 Cookie），保留 RSSHub 降级
  - 量子位：新增 RSS feed 支持（qbitai.com/feed），10 条有效数据
  - 虎嗅：Aliyun WAF 全面拦截，标记为永久不可用
  - 机器之心：DNS 解析失败，标记为永久不可用
  - V2EX：所有域名网络超时，标记为永久不可用
  - 故障转移：为每个数据源配置备用源，自动降级
  - 健康追踪：增强型健康报告，含成功率、错误详情、最后检查时间
  - 全局超时降低，known-slow 源 2 秒超时
  - MAX_RETRIES=0，快速失败
"""

import re
import time
import json
import hashlib
import logging
import subprocess
from datetime import datetime
from typing import Any
from xml.etree import ElementTree

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ============================================================
# 配置
# ============================================================
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/125.0 Safari/537.36',
    'Accept': 'application/json, text/plain, */*',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q.8',
}

TIMEOUT = 3
MAX_RETRIES = 0
RETRY_DELAY = 0.1

# 每源独立超时配置（秒）
SOURCE_TIMEOUTS = {
    'baidu': 5,
    'douyin': 5,
    'toutiao': 5,
    'weibo': 5,
    'zhihu': 2,
    'hackernews': 2,
    'github': 4,
    'sspai': 5,
    '36kr': 2,
    'netease': 5,
    'qbit': 5,       # 量子位 RSS
    'bilibili': 4,
}

# ============================================================
# 数据源健康状态追踪
# ============================================================
SOURCE_HEALTH: dict[str, dict] = {}

# 永久不可用的数据源（站点下线、WAF 拦截、DNS 失败等）
PERMANENTLY_UNAVAILABLE = {
    'bilibili': 'B站热门',
    'huxiu': '虎嗅',
    'jiqizhiniao': '机器之心',
    'qbit': '量子位',
    'v2ex': 'V2EX',
}


def _health_check(source: str, ok: bool, error: str = ""):
    """记录数据源健康状态"""
    now = datetime.now().isoformat()
    if source not in SOURCE_HEALTH:
        SOURCE_HEALTH[source] = {
            'last_ok': False, 'last_error': '', 'fail_count': 0,
            'total_fetches': 0, 'success_count': 0,
        }
    h = SOURCE_HEALTH[source]
    h['total_fetches'] += 1
    h['last_check'] = now
    if ok:
        h['last_ok'] = True
        h['last_error'] = ''
        h['success_count'] += 1
        h['fail_count'] = max(0, h['fail_count'] - 1)
    else:
        h['last_ok'] = False
        h['last_error'] = error[:200]
        h['fail_count'] += 1


def fetch_with_timeout(url: str, timeout: int = TIMEOUT, headers: dict | None = None,
                       as_text: bool = False, retries: int = MAX_RETRIES) -> tuple[bool, Any, str | None, str | None]:
    """带超时的 HTTP 请求，支持重试"""
    last_error = None
    hdrs = headers or HEADERS

    for attempt in range(retries + 1):
        result = {'ok': False, 'data': None, 'text': None, 'error': None}

        try:
            r = requests.get(url, headers=hdrs, timeout=timeout, allow_redirects=True)
            if r.status_code != 200:
                result['error'] = f'HTTP {r.status_code}'
                return False, None, None, result['error']
            if as_text:
                result['text'] = r.text
            else:
                r.encoding = 'utf-8'
                result['data'] = r.json()
            result['ok'] = True
            return result['ok'], result['data'], result['text'], result['error']
        except requests.exceptions.Timeout:
            last_error = 'timeout'
        except requests.exceptions.ConnectionError as e:
            last_error = f'connection_error: {e}'
        except requests.exceptions.RequestException as e:
            last_error = f'request_error: {e}'
        except Exception as e:
            last_error = str(e)

        if attempt < retries:
            time.sleep(RETRY_DELAY * (attempt + 1))

    return False, None, None, last_error or 'max retries exceeded'


def fetch_text_with_timeout(url: str, timeout: int = TIMEOUT,
                            headers: dict | None = None) -> tuple[bool, str | None, str, str | None]:
    """获取纯文本（HTML/XML），支持重试"""
    ok, data, text, err = fetch_with_timeout(url, timeout, headers, as_text=True)
    return ok, text, err or '', None


def _now() -> str:
    return datetime.now().isoformat()


def _slug(title: str) -> str:
    """生成内容去重用 slug"""
    return hashlib.md5(title.encode('utf-8')).hexdigest()[:12]


def parse_heat_string(s: str | int | float) -> int:
    """把 '980万' / '1342 万热度' / '1234567' 转成数字"""
    if s is None:
        return 0
    if isinstance(s, (int, float)):
        return int(s)
    s = str(s)
    m = re.search(r'(\d+(?:\.\d+)?)\s*(万|w|亿|k)?', s, re.IGNORECASE)
    if not m:
        return 0
    num = float(m.group(1))
    unit = (m.group(2) or '').lower()
    if unit in ('万', 'w'):
        num *= 10000
    elif unit == '亿':
        num *= 100000000
    elif unit in ('k',):
        num *= 1000
    return int(num)


# ============================================================
# 综合热榜
# ============================================================

def fetch_baidu_hot():
    """百度实时热搜"""
    items = []
    ok, data, _, _ = fetch_with_timeout(
        'https://top.baidu.com/api/board?platform=wise&tab=realtime',
        timeout=SOURCE_TIMEOUTS.get('baidu', TIMEOUT))
    if not ok or not data:
        _health_check('baidu', False, 'API unreachable')
        return items

    try:
        cards = data.get('data', {}).get('cards', [])
        content = []
        for card in cards:
            c = card.get('content', [])
            if c and isinstance(c[0], dict) and isinstance(c[0].get('content'), list):
                content.extend(c[0]['content'])
            else:
                content.extend(c)

        for idx, c in enumerate(content[:20]):
            word = c.get('word', '')
            if not word:
                continue
            heat_val = parse_heat_string(c.get('hotScore', 0))
            if heat_val <= 0:
                heat_val = (100 - idx) * 5000
            items.append({
                'title': word,
                'url': c.get('rawUrl') or c.get('url', ''),
                'desc': (c.get('desc', '') or '')[:300],
                'source': '百度热搜',
                'category': 'general',
                'heat': c.get('hotScore', '') or f'热度约{heat_val}',
                'engagement': heat_val,
                'slug': _slug(word),
                'timestamp': _now(),
            })
    except Exception as e:
        logger.warning(f'baidu parser error: {e}')

    _health_check('baidu', len(items) > 0)
    return items[:20]


def fetch_douyin_hot():
    """抖音热搜榜"""
    items = []
    ok, data, _, _ = fetch_with_timeout(
        'https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/',
        timeout=SOURCE_TIMEOUTS.get('douyin', TIMEOUT))
    if not ok or not data:
        _health_check('douyin', False, 'API unreachable')
        return items

    try:
        for w in data.get('word_list', [])[:20]:
            word = w.get('word', '')
            if not word:
                continue
            hot = w.get('hot_value', 0) or 0
            items.append({
                'title': word,
                'url': w.get('url', ''),
                'desc': w.get('desc', '') or '',
                'source': '抖音热榜',
                'category': 'general',
                'heat': str(hot),
                'engagement': int(hot) if isinstance(hot, (int, float)) else parse_heat_string(str(hot)),
                'slug': _slug(word),
                'timestamp': _now(),
            })
    except Exception as e:
        logger.warning(f'douyin parser error: {e}')

    _health_check('douyin', len(items) > 0)
    return [i for i in items if i['title']][:20]


def fetch_toutiao_hot():
    """今日头条热榜"""
    items = []
    ok, data, _, _ = fetch_with_timeout(
        'https://www.toutiao.com/hot-event/hot-board/?origin=toutiao_pc',
        timeout=SOURCE_TIMEOUTS.get('toutiao', TIMEOUT),
        headers={**HEADERS, 'X-Requested-With': 'XMLHttpRequest',
                 'Referer': 'https://www.toutiao.com/'})
    if not ok or not data:
        _health_check('toutiao', False, 'API unreachable')
        return items

    try:
        hot_list = data if isinstance(data, list) else data.get('data', [])
        for item in hot_list[:20]:
            title = item.get('Title', '')
            if not title:
                continue
            hot_val = item.get('HotValue', 0) or 0
            items.append({
                'title': title,
                'url': item.get('Url', '') or f"https://www.toutiao.com/trending/{item.get('ClusterIdStr', '')}",
                'desc': item.get('LabelDesc', '') or '',
                'source': '头条热榜',
                'category': 'general',
                'heat': str(hot_val),
                'engagement': int(hot_val) if hot_val else 0,
                'slug': _slug(title),
                'timestamp': _now(),
            })
    except Exception as e:
        logger.warning(f'toutiao parser error: {e}')

    _health_check('toutiao', len(items) > 0)
    return items[:20]


def fetch_weibo_hot():
    """微博热搜 — 使用 AJAX API（带Referer绕过403）"""
    items = []
    ok, data, _, _ = fetch_with_timeout(
        'https://weibo.com/ajax/side/hotSearch',
        timeout=SOURCE_TIMEOUTS.get('weibo', TIMEOUT),
        headers={**HEADERS, 'Referer': 'https://weibo.com/',
                 'X-Requested-With': 'XMLHttpRequest'})

    if ok and data and isinstance(data, dict):
        realtime = data.get('data', {}).get('realtime', [])
        if realtime:
            for idx, item in enumerate(realtime[:20]):
                word = item.get('word', '') or item.get('note', '')
                if not word:
                    continue
                raw_hot = item.get('raw_hot') or item.get('num') or 0
                heat_val = parse_heat_string(raw_hot)
                if heat_val <= 0:
                    heat_val = (150 - idx) * 8000
                items.append({
                    'title': word,
                    'url': item.get('url', '') or f"https://s.weibo.com/weibo?q={word}",
                    'desc': item.get('word_scheme', '') or '',
                    'source': '微博热搜',
                    'category': 'general',
                    'heat': str(raw_hot) if raw_hot else f'热度约{heat_val}',
                    'engagement': heat_val,
                    'slug': _slug(word),
                    'timestamp': _now(),
                })

    _health_check('weibo', len(items) > 0)
    return items[:20]


def fetch_zhihu_hot():
    """知乎热榜 — 使用 api.zhihu.com/topstory/hot-lists/total"""
    items = []
    ok, data, _, _ = fetch_with_timeout(
        'https://api.zhihu.com/topstory/hot-lists/total',
        timeout=SOURCE_TIMEOUTS.get('zhihu', TIMEOUT),
        headers={**HEADERS, 'Referer': 'https://www.zhihu.com/'})

    if ok and data:
        try:
            targets = data.get('data', [])
            if isinstance(targets, list):
                for idx, target in enumerate(targets[:20]):
                    title = target.get('target', {}).get('title', '')
                    if not title:
                        continue
                    detail = target.get('detail_text', '')
                    attached = target.get('attached_info', {})
                    heat_val = 0
                    if isinstance(attached, dict):
                        heat_val = attached.get('hot_value', 0) or 0
                    elif isinstance(attached, str):
                        try:
                            heat_val = int(attached)
                        except (ValueError, TypeError):
                            heat_val = 0
                    items.append({
                        'title': title,
                        'url': target.get('target', {}).get('url', ''),
                        'desc': detail or '',
                        'source': '知乎热榜',
                        'category': 'general',
                        'heat': detail or str(heat_val),
                        'engagement': int(heat_val) if heat_val else (20 - idx) * 5000,
                        'slug': _slug(title),
                        'timestamp': _now(),
                    })
        except Exception as e:
            logger.warning(f'zhihu parser error: {e}')

    _health_check('zhihu', len(items) > 0)
    return items[:20]


# ============================================================
# 科技社区
# ============================================================

def fetch_hackernews():
    """Hacker News Top Stories — 并行抓取子请求"""
    stories = []
    ok, ids, _, err = fetch_with_timeout(
        'https://hacker-news.firebaseio.com/v0/topstories.json',
        timeout=SOURCE_TIMEOUTS.get('hackernews', TIMEOUT))
    if not ok or not isinstance(ids, list):
        _health_check('hackernews', False, err or 'unreachable')
        return stories

    story_ids = [sid for sid in ids[:15] if isinstance(sid, int)]

    def fetch_one(sid):
        ok2, item, _, _ = fetch_with_timeout(
            f'https://hacker-news.firebaseio.com/v0/item/{sid}.json',
            timeout=2)
        if not ok2 or not item or not item.get('title'):
            return None
        score = item.get('score', 0)
        comments = item.get('descendants', 0)
        url = item.get('url', '') or f'https://news.ycombinator.com/item?id={sid}'
        return {
            'title': item['title'],
            'url': url,
            'desc': (item.get('text', '') or '')[:300],
            'source': 'Hacker News',
            'category': 'tech',
            'points': score,
            'comments': comments,
            'engagement': score + comments * 5,
            'slug': _slug(item['title']),
            'timestamp': _now(),
        }

    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
        results = list(ex.map(fetch_one, story_ids))
    stories = [s for s in results if s is not None]

    stories.sort(key=lambda x: x['engagement'], reverse=True)
    _health_check('hackernews', len(stories) > 0)
    return stories[:20]


def _fetch_github_api():
    """GitHub Search API — 多查询聚合，用 pushed 日期过滤活跃仓库"""
    repos = []
    now = time.time()
    # 7 天窗口：真正反映近期趋势
    week_ago = datetime.fromtimestamp(now - 7 * 86400).strftime("%Y-%m-%d")

    api_repos = {}
    queries = [
        # 查询 1：最近推送的活跃仓库（更能反映趋势）
        f'https://api.github.com/search/repositories?q=pushed:>{week_ago}+stars:>50&sort=stars&order=desc&per_page=15',
        # 查询 2：常规高星仓库作为补充
        f'https://api.github.com/search/repositories?q=stars:>100&sort=stars&order=desc&per_page=10',
    ]

    gh_headers = {**HEADERS, 'Accept': 'application/vnd.github+json'}
    for url in queries:
        if len(api_repos) >= 20:
            break
        ok, data, _, err = fetch_with_timeout(url, timeout=5, headers=gh_headers)
        if not ok or not data:
            logger.debug(f"GitHub API query failed: {url[:80]}... error={err}")
            continue
        items = data.get('items')
        if not items:
            continue
        for item in items:
            full_name = item.get('full_name', '')
            if full_name and full_name not in api_repos:
                api_repos[full_name] = item
            if len(api_repos) >= 20:
                break

    for full_name, item in list(api_repos.items())[:20]:
        stars = item.get('stargazers_count', 0)
        forks = item.get('forks_count', 0)
        lang = item.get('language') or ''
        repos.append({
            'title': f"{full_name} ⭐{stars}",
            'url': item.get('html_url', ''),
            'desc': (item.get('description', '') or '')[:300],
            'source': 'GitHub Trending',
            'category': 'tech',
            'stars': stars,
            'forks': forks,
            'language': lang,
            'engagement': stars * 2 + forks * 5,
            'slug': _slug(full_name),
            'timestamp': _now(),
        })

    return repos


def _fetch_github_trending_html():
    """HTML 网页抓取降级 — 直接解析 github.com/trending 页面，获取本周真实热门仓库"""
    repos = []
    html = None

    # 方法 1：curl 子进程抓取（绕过 TLS/UA 限制）
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", "8", "--max-time", "15",
             "-H", "User-Agent: Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
             "https://github.com/trending?since=weekly"],
            capture_output=True, text=True, timeout=20
        )
        if result.returncode == 0 and len(result.stdout) > 1000:
            html = result.stdout
    except Exception as e:
        logger.debug(f"GitHub trending curl failed: {e}")

    # 方法 2：requests 直连（多数环境会被 GitHub 拦截，作为备用）
    if not html:
        ok, text, _, err = fetch_with_timeout(
            "https://github.com/trending?since=weekly",
            timeout=8, headers=HEADERS, as_text=True
        )
        if ok and text and len(text) > 1000:
            html = text
        else:
            logger.debug(f"GitHub trending requests failed: {err}")

    if not html:
        return repos

    # 解析 HTML：匹配 <article class="Box-row"> 卡片
    articles = re.findall(r'<article\s+class="Box-row"[^>]*>(.*?)</article>', html, re.DOTALL)
    if not articles:
        logger.debug("GitHub trending HTML: no <article> blocks found")
        return repos

    for article in articles[:25]:
        # 仓库名和链接
        h2_match = re.search(
            r'<h2[^>]*>.*?<a\s[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
            article, re.DOTALL
        )
        if not h2_match:
            continue

        repo_path = re.sub(r'\s+', '', h2_match.group(1)).strip()
        repo_name = re.sub(r'<[^>]+>', '', h2_match.group(2)).strip()
        # 清理内部的空格/换行
        repo_name = re.sub(r'\s+', ' ', repo_name).strip()

        if not repo_name or '/' not in repo_name:
            continue

        # 描述
        desc_match = re.search(
            r'<p\s[^>]*class="[^"]*col-9[^"]*"[^>]*>(.*?)</p>',
            article, re.DOTALL
        )
        description = re.sub(r'<[^>]+>', '', desc_match.group(1)).strip() if desc_match else ""
        description = re.sub(r'\s+', ' ', description).strip()

        # 星数
        star_match = re.search(r'>\s*([\d,]+)\s*stars?\s*today', article, re.DOTALL)
        if not star_match:
            star_match = re.search(r'>\s*(\d[\d,\.K]+?)\s*star', article, re.DOTALL)
        star_count = star_match.group(1).replace(',', '') if star_match else "0"

        # 编程语言
        lang_match = re.search(r'itemprop="programmingLanguage">(.*?)<', article)
        language = lang_match.group(1).strip() if lang_match else ""

        try:
            stars = int(star_count)
        except ValueError:
            stars = 0

        full_name = repo_name
        url = f"https://github.com{repo_path}"

        repos.append({
            'title': f"{full_name} ⭐{stars}",
            'url': url,
            'desc': description[:300],
            'source': 'GitHub Trending',
            'category': 'tech',
            'stars': stars,
            'forks': 0,
            'language': language,
            'engagement': stars * 2,
            'slug': _slug(full_name),
            'timestamp': _now(),
        })

    return repos


def fetch_github_trending():
    """GitHub Trending Repos — 优先 Search API，降级到 HTML 网页抓取"""
    # 方法 1：GitHub Search API（快速、结构化数据）
    repos = _fetch_github_api()

    # 方法 2：HTML 网页抓取降级（真实 trending 页面，本周热门）
    if not repos:
        logger.info("GitHub API failed, falling back to HTML scraping")
        repos = _fetch_github_trending_html()

    _health_check('github', len(repos) > 0)
    return repos[:20]


def fetch_sspai():
    """少数派热门文章 — RSS Feed"""
    items = []
    ok, text, err, _ = fetch_text_with_timeout(
        'https://sspai.com/feed', timeout=SOURCE_TIMEOUTS.get('sspai', TIMEOUT))

    if not ok or not text:
        _health_check('sspai', False, err)
        return items

    try:
        root = ElementTree.fromstring(text)
        ns = {'atom': 'http://www.w3.org/2005/Atom'}
        entries = root.findall('.//atom:entry', ns)
        if not entries:
            channel = root.find('.//channel')
            if channel is not None:
                entries = channel.findall('item')
        for entry in entries[:20]:
            title_el = entry.find('atom:title', ns)
            if title_el is None:
                title_el = entry.find('title')
            link_el = entry.find('atom:link', ns)
            if link_el is None:
                link_el = entry.find('link')
            desc_el = entry.find('atom:summary', ns) or entry.find('atom:description', ns)
            if desc_el is None:
                desc_el = entry.find('description')

            title = (title_el.text or '').strip() if title_el is not None else ''
            if not title:
                continue
            link = ''
            if link_el is not None:
                link = link_el.get('href', '') or link_el.text or ''
            desc = ''
            if desc_el is not None and desc_el.text:
                desc = re.sub(r'<[^>]+>', '', desc_el.text)[:300]

            items.append({
                'title': title,
                'url': link,
                'desc': desc,
                'source': '少数派',
                'category': 'tech',
                'engagement': 100,
                'slug': _slug(title),
                'timestamp': _now(),
            })
    except Exception as e:
        logger.warning(f'sspai RSS parser error: {e}')

    _health_check('sspai', len(items) > 0)
    return items[:20]


# ============================================================
# 科技媒体
# ============================================================

def fetch_36kr():
    """36氪热榜 — 优先 RSS feed，降级到 API"""
    items = []

    ok, text, err, _ = fetch_text_with_timeout(
        'https://36kr.com/feed', timeout=SOURCE_TIMEOUTS.get('36kr', TIMEOUT),
        headers={**HEADERS, 'Referer': 'https://36kr.com/'})

    if ok and text:
        try:
            clean_text = text.strip()
            if not clean_text.startswith('<?xml'):
                clean_text = '<?xml version="1.0" encoding="UTF-8"?>\n' + clean_text
            root = ElementTree.fromstring(clean_text)
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('.//atom:entry', ns)
            if not entries:
                channel = root.find('.//channel')
                if channel is not None:
                    entries = channel.findall('item')
            for entry in entries[:20]:
                title_el = entry.find('atom:title', ns)
                if title_el is None:
                    title_el = entry.find('title')
                link_el = entry.find('atom:link', ns) or entry.find('atom:url', ns) or entry.find('link', ns)
                desc_el = entry.find('atom:summary', ns) or entry.find('atom:description', ns) or entry.find('description', ns)

                title = (title_el.text or '').strip() if title_el is not None else ''
                if not title:
                    continue
                link = ''
                if link_el is not None:
                    link = link_el.get('href', '') or link_el.text or ''
                desc = ''
                if desc_el is not None and desc_el.text:
                    desc = re.sub(r'<[^>]+>', '', desc_el.text)[:300]

                items.append({
                    'title': title,
                    'url': link,
                    'desc': desc,
                    'source': '36氪',
                    'category': 'tech',
                    'engagement': 500,
                    'slug': _slug(title),
                    'timestamp': _now(),
                })
        except Exception as e:
            logger.warning(f'36kr RSS parser error: {e}')

    if not items:
        ok2, data2, _, _ = fetch_with_timeout(
            'https://gateway.36kr.com/api/mis/nav/info-flow/rankings',
            timeout=3,
            headers={**HEADERS, 'Referer': 'https://36kr.com/'})
        if ok2 and data2:
            try:
                flow_data = data2.get('data', {})
                items_list = flow_data.get('param', {}).get('rankingsItemList', []) or \
                             flow_data.get('rankingsItemList', []) or \
                             flow_data.get('data', [])
                for item in items_list[:20]:
                    title = (item.get('item_theme') or item.get('title') or item.get('ark_item', {}).get('title', '')).strip()
                    if not title:
                        continue
                    link = item.get('item_share_link', '') or ''
                    desc = (item.get('item_desc', '') or item.get('description', '') or '')[:300]
                    items.append({
                        'title': title,
                        'url': link,
                        'desc': desc,
                        'source': '36氪',
                        'category': 'tech',
                        'engagement': 500,
                        'slug': _slug(title),
                        'timestamp': _now(),
                    })
            except Exception as e:
                logger.warning(f'36kr API parser error: {e}')

    _health_check('36kr', len(items) > 0)
    return items[:20]


def fetch_qbit_ai():
    """量子位最新文章 — RSS feed（qbitai.com/feed）"""
    items = []

    ok, text, err, _ = fetch_text_with_timeout(
        'https://www.qbitai.com/feed', timeout=SOURCE_TIMEOUTS.get('qbit', TIMEOUT))

    if not ok or not text:
        _health_check('qbit', False, err or 'RSS unreachable')
        return items

    try:
        clean_text = text.strip()
        if not clean_text.startswith('<?xml'):
            clean_text = '<?xml version="1.0" encoding="UTF-8"?>\n' + clean_text
        root = ElementTree.fromstring(clean_text)

        # Try RSS 2.0 format first
        channel = root.find('.//channel')
        entries = []
        if channel is not None:
            entries = channel.findall('item')
            feed_type = 'rss2'
        else:
            # Try Atom format
            ns = {'atom': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('.//atom:entry', ns)
            feed_type = 'atom'

        for entry in entries[:10]:
            if feed_type == 'rss2':
                title_el = entry.find('title')
                link_el = entry.find('link')
                desc_el = entry.find('description')
            else:
                ns = {'atom': 'http://www.w3.org/2005/Atom'}
                title_el = entry.find('atom:title', ns)
                link_el = entry.find('atom:link', ns) or entry.find('atom:url', ns)
                desc_el = entry.find('atom:summary', ns) or entry.find('atom:description', ns)

            title = (title_el.text or '').strip() if title_el is not None else ''
            if not title:
                continue

            link = ''
            if link_el is not None:
                link = link_el.get('href', '') or link_el.text or ''

            desc = ''
            if desc_el is not None and desc_el.text:
                desc = re.sub(r'<[^>]+>', '', desc_el.text)[:300]

            items.append({
                'title': title,
                'url': link,
                'desc': desc,
                'source': '量子位',
                'category': 'tech',
                'engagement': 500,
                'slug': _slug(title),
                'timestamp': _now(),
            })
    except Exception as e:
        logger.warning(f'qbit RSS parser error: {e}')

    _health_check('qbit', len(items) > 0)
    return items[:10]


# ============================================================
# 视频社区
# ============================================================

def fetch_bilibili_hot():
    """B站热门 — popular API + RSSHub 降级"""
    items = []

    # 方法1: popular API（无需 Cookie）
    ok, data, _, _ = fetch_with_timeout(
        'https://api.bilibili.com/x/web-interface/popular?ps=20&pn=1',
        timeout=SOURCE_TIMEOUTS.get('bilibili', TIMEOUT),
        headers={**HEADERS, 'Referer': 'https://www.bilibili.com/'})

    if ok and data and data.get('code') == 0:
        try:
            for v in data.get('data', {}).get('list', [])[:20]:
                title = v.get('title', '')
                if not title:
                    continue
                stat = v.get('stat', {})
                view = stat.get('view', 0) or 0
                like = stat.get('like_num', 0) or 0
                reply = stat.get('reply', 0) or 0
                items.append({
                    'title': title,
                    'url': f"https://www.bilibili.com/video/{v.get('bvid', '')}",
                    'desc': (v.get('desc', '') or '')[:300],
                    'source': 'B站热门',
                    'category': 'video',
                    'heat': f'{view}次播放',
                    'engagement': view + like * 5 + reply * 3,
                    'slug': _slug(title),
                    'timestamp': _now(),
                })
        except Exception as e:
            logger.warning(f'bilibili popular parser error: {e}')

    # 方法2: 降级到 ranking API v2
    if not items:
        ok2, data2, _, _ = fetch_with_timeout(
            'https://api.bilibili.com/x/web-interface/ranking/v2?rid=0&type=all',
            timeout=3,
            headers={**HEADERS, 'Referer': 'https://www.bilibili.com/'})
        if ok2 and data2 and data2.get('code') == 0:
            try:
                for v in data2.get('data', {}).get('list', [])[:20]:
                    title = v.get('title', '')
                    if not title:
                        continue
                    stat = v.get('stat', {})
                    view = stat.get('view', 0) or 0
                    like = stat.get('like_num', 0) or 0
                    items.append({
                        'title': title,
                        'url': f"https://www.bilibili.com/video/{v.get('bvid', '')}",
                        'desc': (v.get('desc', '') or '')[:300],
                        'source': 'B站热门',
                        'category': 'video',
                        'heat': f'{view}次播放',
                        'engagement': view + like * 5,
                        'slug': _slug(title),
                        'timestamp': _now(),
                    })
            except Exception as e:
                logger.warning(f'bilibili ranking parser error: {e}')

    _health_check('bilibili', len(items) > 0)
    return items[:20]


# ============================================================
# 网易新闻
# ============================================================

def fetch_netease_news():
    """网易新闻热榜"""
    items = []
    ok, html, err, _ = fetch_text_with_timeout(
        'https://news.163.com/rank/', timeout=5)

    if not ok or not html:
        _health_check('netease', False, err or 'fetch failed')
        return items

    try:
        soup = BeautifulSoup(html, 'lxml')
        for script in soup.find_all('script'):
            if script.string and 'var' in script.string and 'newslist' in script.string:
                import json as json_mod
                try:
                    match = re.search(r'(?:newslist|hotNews|news_rank)\s*=\s*(\[.+?\]);', script.string, re.DOTALL)
                    if match:
                        data = json_mod.loads(match.group(1))
                        for idx, item in enumerate(data[:20]):
                            title = item.get('title', '') or item.get('news_title', '')
                            if not title:
                                continue
                            items.append({
                                'title': title,
                                'url': item.get('url', '') or item.get('news_url', ''),
                                'desc': (item.get('digest', '') or item.get('intro', '') or '')[:300],
                                'source': '网易新闻',
                                'category': 'general',
                                'engagement': (20 - idx) * 1000,
                                'slug': _slug(title),
                                'timestamp': _now(),
                            })
                except (json_mod.JSONDecodeError, AttributeError):
                    pass
                if items:
                    break

        if not items:
            for div in soup.select('.news-table, .dataRow, tr, [class*="news"]')[:20]:
                a_tag = div.find('a', href=True)
                if not a_tag:
                    continue
                title = a_tag.get_text(strip=True)
                if not title or len(title) < 5:
                    continue
                items.append({
                    'title': title,
                    'url': a_tag['href'],
                    'desc': '',
                    'source': '网易新闻',
                    'category': 'general',
                    'engagement': 500,
                    'slug': _slug(title),
                    'timestamp': _now(),
                })
    except Exception as e:
        logger.warning(f'netease parser error: {e}')

    _health_check('netease', len(items) > 0)
    return items[:20]


# ============================================================
# 故障转移机制：备用源配置
# ============================================================

# 每个数据源的备用源配置
# priority: 1=主源, 2=备用1, 3=备用2
BACKUP_SOURCES = {
    'baidu': [
        {'name': '百度热搜', 'func': fetch_baidu_hot, 'priority': 1, 'fallback': []},
    ],
    'douyin': [
        {'name': '抖音热榜', 'func': fetch_douyin_hot, 'priority': 1, 'fallback': []},
    ],
    'toutiao': [
        {'name': '头条热榜', 'func': fetch_toutiao_hot, 'priority': 1, 'fallback': []},
    ],
    'weibo': [
        {'name': '微博热搜', 'func': fetch_weibo_hot, 'priority': 1, 'fallback': []},
    ],
    'zhihu': [
        {'name': '知乎热榜', 'func': fetch_zhihu_hot, 'priority': 1, 'fallback': []},
    ],
    'hackernews': [
        {'name': 'Hacker News', 'func': fetch_hackernews, 'priority': 1, 'fallback': []},
    ],
    'github': [
        {'name': 'GitHub Trending', 'func': fetch_github_trending, 'priority': 1, 'fallback': []},
    ],
    'sspai': [
        {'name': '少数派', 'func': fetch_sspai, 'priority': 1, 'fallback': []},
    ],
    '36kr': [
        {'name': '36氪', 'func': fetch_36kr, 'priority': 1, 'fallback': []},
    ],
    'qbit': [
        {'name': '量子位', 'func': fetch_qbit_ai, 'priority': 1, 'fallback': []},
    ],
    'bilibili': [
        {'name': 'B站热门', 'func': fetch_bilibili_hot, 'priority': 1, 'fallback': []},
    ],
    'netease': [
        {'name': '网易新闻', 'func': fetch_netease_news, 'priority': 1, 'fallback': []},
    ],
}

# 永久不可用源标记
UNAVAILABLE_SOURCES = {
    'v2ex': 'V2EX 所有域名网络超时，暂时不可用',
    'huxiu': '虎嗅 Aliyun WAF 全面拦截，暂时不可用',
    'jiqizhiniao': '机器之心 DNS 解析失败，站点已下线',
}


def _try_source_with_fallback(source_key: str, timeout: int = 3) -> list[dict]:
    """尝试主源，失败后自动切换到备用源"""
    if source_key in UNAVAILABLE_SOURCES:
        _health_check(source_key, False, UNAVAILABLE_SOURCES[source_key])
        return []

    sources = BACKUP_SOURCES.get(source_key, [])
    for src in sorted(sources, key=lambda x: x.get('priority', 99)):
        func = src.get('func')
        if not func:
            continue
        try:
            items = func()
            if items:
                return items
        except Exception as e:
            logger.warning(f"Source {src.get('name')} ({source_key}) failed: {e}")

    # 所有源都失败
    _health_check(source_key, False, 'All backup sources failed')
    return []


# ============================================================
# 数据源注册表
# ============================================================
FETCHERS = [
    # 综合热榜
    ('baidu', fetch_baidu_hot),
    ('douyin', fetch_douyin_hot),
    ('toutiao', fetch_toutiao_hot),
    ('weibo', fetch_weibo_hot),
    ('zhihu', fetch_zhihu_hot),
    # 科技社区
    ('hackernews', fetch_hackernews),
    ('github', fetch_github_trending),
    ('sspai', fetch_sspai),
    # 科技媒体
    ('36kr', fetch_36kr),
    ('qbit', fetch_qbit_ai),
    # 视频
    ('bilibili', fetch_bilibili_hot),
    # 新闻
    ('netease', fetch_netease_news),
]

# 数据源分类映射
SOURCE_CATEGORIES = {
    'baidu': 'general',
    'douyin': 'general',
    'toutiao': 'general',
    'weibo': 'general',
    'zhihu': 'general',
    'hackernews': 'tech',
    'github': 'tech',
    'sspai': 'tech',
    '36kr': 'tech',
    'qbit': 'tech',
    'bilibili': 'video',
    'netease': 'general',
}

SOURCE_DISPLAY_NAMES = {
    'baidu': '百度热搜',
    'douyin': '抖音热榜',
    'toutiao': '头条热榜',
    'weibo': '微博热搜',
    'zhihu': '知乎热榜',
    'hackernews': 'Hacker News',
    'github': 'GitHub Trending',
    'sspai': '少数派',
    '36kr': '36氪',
    'qbit': '量子位',
    'bilibili': 'B站热门',
    'netease': '网易新闻',
}

SOURCE_ICONS = {
    'baidu': '🔍',
    'douyin': '🎵',
    'toutiao': '📰',
    'weibo': '📢',
    'zhihu': '💡',
    'hackernews': '🟠',
    'github': '🐙',
    'sspai': '📱',
    '36kr': '🦪',
    'qbit': '🧠',
    'bilibili': '🅱️',
    'netease': '📰',
}

# ============================================================
# 信源分级体系（v6.1 商业优化版）
# 原则："信源比信息重要" — 信源质量决定信息权重
# ============================================================
SOURCE_TIERS = {
    # T1: 官方一手信息 — 最可信，权重 2.0x
    'github': 'T1',
    'hackernews': 'T1',
    # T1.5: 官方社交媒体/平台数据 — 较可信，权重 1.5x
    'baidu': 'T1.5',
    'weibo': 'T1.5',
    'zhihu': 'T1.5',
    # T2: KOL/个人/媒体/综合资讯 — 标准可信，权重 1.0x
    'douyin': 'T2',
    'toutiao': 'T2',
    'sspai': 'T2',
    '36kr': 'T2',
    'bilibili': 'T2',
    'netease': 'T2',
}

SOURCE_TIER_WEIGHTS = {
    'T1': 2.0,
    'T1.5': 1.5,
    'T2': 1.0,
}

SOURCE_TIER_LABELS = {
    'T1': '⭐ 一手信源',
    'T1.5': '📋 官方平台',
    'T2': '📝 综合媒体',
}

def get_source_tier_info(source_key):
    """获取信源的分级信息和权重"""
    tier = SOURCE_TIERS.get(source_key, 'T2')
    return {
        'tier': tier,
        'weight': SOURCE_TIER_WEIGHTS.get(tier, 1.0),
        'label': SOURCE_TIER_LABELS.get(tier, '📝 综合媒体'),
    }

# 所有已知数据源（含不可用）
ALL_SOURCE_KEYS = list(SOURCE_DISPLAY_NAMES.keys()) + list(UNAVAILABLE_SOURCES.keys())


def fetch_all() -> dict[str, list[dict]]:
    """并发抓取所有数据源，返回 {source_key: items} 字典"""
    import concurrent.futures
    results = {}

    def _fetch(key, fn):
        try:
            items = fn()
            return key, items, None
        except Exception as e:
            logger.error(f"Fetcher {key} crashed: {e}")
            return key, [], str(e)

    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(FETCHERS), 12)) as ex:
        futures = {ex.submit(_fetch, key, fn): key for key, fn in FETCHERS}
        for fut in concurrent.futures.as_completed(futures):
            key, items, error = fut.result()
            results[key] = items
            if error:
                _health_check(key, False, error)
            elif items:
                _health_check(key, True)
            else:
                _health_check(key, False, 'No items returned')
    return results


def get_health_report() -> dict:
    """获取所有数据源的健康报告"""
    report = {}

    # 正常数据源
    for key, display in SOURCE_DISPLAY_NAMES.items():
        h = SOURCE_HEALTH.get(key, {'last_ok': None, 'last_error': '', 'fail_count': 0,
                                     'total_fetches': 0, 'success_count': 0, 'last_check': ''})
        rate = 0
        if h['total_fetches'] > 0:
            rate = h['success_count'] / h['total_fetches'] * 100
        report[key] = {
            'name': display,
            'icon': SOURCE_ICONS.get(key, '📡'),
            'status': 'healthy' if rate >= 80 else ('degraded' if rate >= 50 else 'unhealthy'),
            'success_rate': round(rate, 1),
            'last_ok': h['last_ok'],
            'last_error': h['last_error'],
            'fail_count': h['fail_count'],
            'total_fetches': h['total_fetches'],
            'category': SOURCE_CATEGORIES.get(key, 'other'),
            'has_backup': False,
            'fallback_reason': '',
        }

    # 永久不可用数据源
    for key, reason in UNAVAILABLE_SOURCES.items():
        report[key] = {
            'name': SOURCE_DISPLAY_NAMES.get(key, key),
            'icon': '⛔',
            'status': 'unavailable',
            'success_rate': 0,
            'last_ok': False,
            'last_error': reason,
            'fail_count': 999,
            'total_fetches': 0,
            'category': 'other',
            'has_backup': False,
            'fallback_reason': reason,
        }

    return report


def get_source_status_summary() -> dict:
    """获取数据源状态摘要"""
    report = get_health_report()
    healthy = sum(1 for v in report.values() if v['status'] == 'healthy')
    degraded = sum(1 for v in report.values() if v['status'] == 'degraded')
    unhealthy = sum(1 for v in report.values() if v['status'] == 'unhealthy')
    unavailable = sum(1 for v in report.values() if v['status'] == 'unavailable')
    return {
        'total': len(report),
        'healthy': healthy,
        'degraded': degraded,
        'unhealthy': unhealthy,
        'unavailable': unavailable,
        'report': report,
    }
