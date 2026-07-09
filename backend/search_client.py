"""
搜索 API 客户端模块 (v6.1)

支持四种搜索引擎：
- DuckDuckGo（默认，免 API Key，多端点自动降级）
- Bing HTML 抓取（免 API Key，国内服务器友好）
- SerpAPI（可选，需 API Key）
- Bing Web Search API v7（可选，需 API Key）

多端点降级策略：
  lite.duckduckgo.com → duckduckgo.com/html → Bing HTML 抓取 → 返回空

统一接口：search(query, num_results=10) -> [{title, url, snippet}]
超时 8 秒 + 重试，失败返回空列表（不阻塞工作流）。
"""
import re
import time
from urllib.parse import quote_plus

from config import get_config


def _get_proxies():
    """从配置中构建代理字典"""
    cfg = get_config()
    proxy_url = cfg.get('proxy', '')
    if proxy_url:
        return {'http': proxy_url, 'https': proxy_url}
    return None


def _http_get(url, params=None, headers=None, timeout=8, data=None):
    """统一的 HTTP 请求封装，带重试"""
    import requests
    proxies = _get_proxies()
    for attempt in range(2):
        try:
            if data:
                resp = requests.post(url, data=data, headers=headers or {},
                                     timeout=timeout, proxies=proxies)
            else:
                resp = requests.get(url, params=params, headers=headers or {},
                                    timeout=timeout, proxies=proxies)
            return resp
        except requests.exceptions.Timeout:
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
        except requests.exceptions.ConnectionError:
            if attempt == 0:
                time.sleep(0.5)
                continue
            raise
    return None


def search(query, num_results=10):
    """
    统一搜索接口。根据配置选择搜索引擎或自动降级。

    优先级：Tavily（有 key 时自动优先） > 用户选择的 provider > DuckDuckGo 降级
    返回 [{title, url, snippet}]，失败返回空列表。
    """
    cfg = get_config()
    provider = cfg.get('search_provider', 'bing_html').lower()
    api_key = cfg.get('search_api_key', '')
    tavily_key = cfg.get('tavily_api_key', '')

    try:
        # Tavily 配置后自动优先，无论 search_provider 选了什么
        if tavily_key:
            return _search_tavily(query, tavily_key, num_results)

        if provider == 'serpapi' and api_key:
            return _search_serpapi(query, api_key, num_results)
        elif provider == 'bing' and api_key:
            return _search_bing_api(query, api_key, num_results)
        elif provider == 'bing_html':
            return _search_bing_html(query, num_results)
        else:
            return _search_duckduckgo_robust(query, num_results)
    except Exception as e:
        print(f"[search_client] search failed ({provider}): {e}")
        return []


# ========================================
# DuckDuckGo — 多端点降级策略
# ========================================

def _search_duckduckgo_robust(query, num_results=10):
    """
    DuckDuckGo 鲁棒搜索：依次尝试多个端点
    1. lite.duckduckgo.com (最轻量，国内有时可通)
    2. html.duckduckgo.com (原始方案)
    3. 自动降级到 Bing HTML 抓取
    """
    import requests
    import time

    # 端点 1: DuckDuckGo Lite
    try:
        results = _search_duckduckgo_lite(query, num_results)
        if results:
            print(f"[search_client] duckduckgo(lite) OK: {len(results)} results")
            return results
    except Exception as e:
        print(f"[search_client] duckduckgo(lite) failed: {e}")

    # 端点 2: DuckDuckGo HTML (传统方案)
    try:
        results = _search_duckduckgo_html(query, num_results)
        if results:
            print(f"[search_client] duckduckgo(html) OK: {len(results)} results")
            return results
    except Exception as e:
        print(f"[search_client] duckduckgo(html) failed: {e}")

    # 自动降级到 Bing HTML 抓取
    try:
        print(f"[search_client] Falling back to Bing HTML for query: {query[:30]}...")
        results = _search_bing_html(query, num_results)
        if results:
            print(f"[search_client] bing_html(fallback) OK: {len(results)} results")
            return results
    except Exception as e:
        print(f"[search_client] bing_html(fallback) failed: {e}")

    print(f"[search_client] all search providers failed for query: {query[:30]}...")
    return []


def _search_duckduckgo_lite(query, num_results=10):
    """
    DuckDuckGo Lite 端点 — 极简 HTML 页面，国内访问概率更高
    页面格式: <a href="..." class="result-link">title</a> + <span class="result-snippet">desc</span>
    """
    from bs4 import BeautifulSoup

    url = 'https://lite.duckduckgo.com/lite/'
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    resp = _http_get(url, params={'q': query}, headers=headers, timeout=8, data=None)
    if not resp or resp.status_code != 200:
        return []

    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')
    results = []

    # Lite 页面格式: 每个结果是一个 <tr> 包含 <a> 和 <span>
    for row in soup.select('.result-link'):
        if not row.name == 'a':
            row = row.find('a')
        if not row:
            continue
        title = row.get_text(strip=True)
        link = row.get('href', '')
        # 找相邻的 snippet
        snippet = ''
        next_tr = row
        for _ in range(3):
            next_tr = next_tr.next_sibling if hasattr(next_tr, 'next_sibling') else None
            if next_tr and hasattr(next_tr, 'select'):
                snip = next_tr.select_one('.result-snippet')
                if snip:
                    snippet = snip.get_text(strip=True)
                    break

        if title and link:
            # 清理 DuckDuckGo 重定向链接
            if 'uddg=' in link:
                m = re.search(r'uddg=([^&]+)', link)
                if m:
                    from urllib.parse import unquote
                    link = unquote(m.group(1))
            results.append({'title': title, 'url': link, 'snippet': snippet})
            if len(results) >= num_results:
                break

    # fallback: 尝试用更宽松的选择器
    if not results:
        for a_tag in soup.select('a[rel="nofollow"]'):
            title = a_tag.get_text(strip=True)
            link = a_tag.get('href', '')
            if title and link and not link.startswith('//duckduckgo.com'):
                if 'uddg=' in link:
                    m = re.search(r'uddg=([^&]+)', link)
                    if m:
                        from urllib.parse import unquote
                        link = unquote(m.group(1))
                results.append({'title': title, 'url': link, 'snippet': ''})
                if len(results) >= num_results:
                    break

    return results


def _search_duckduckgo_html(query, num_results=10):
    """DuckDuckGo HTML 搜索（传统方案，保留作为备用）"""
    from bs4 import BeautifulSoup

    url = 'https://html.duckduckgo.com/html/'
    params = {'q': query}
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    resp = _http_get(url, params=None, headers=headers, timeout=8, data=params)
    if not resp or resp.status_code != 200:
        return []

    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')
    results = []
    for item in soup.select('.result'):
        title_tag = item.select_one('.result__a')
        snippet_tag = item.select_one('.result__snippet')
        if not title_tag:
            continue
        title = title_tag.get_text(strip=True)
        link = title_tag.get('href', '')
        if 'duckduckgo.com/l/?uddg=' in link:
            m = re.search(r'uddg=([^&]+)', link)
            if m:
                from urllib.parse import unquote
                link = unquote(m.group(1))
        snippet = snippet_tag.get_text(strip=True) if snippet_tag else ''
        results.append({'title': title, 'url': link, 'snippet': snippet})
        if len(results) >= num_results:
            break
    return results


# ========================================
# Bing HTML 抓取 — 免 API Key 免费方案
# ========================================

def _search_bing_html(query, num_results=10):
    """
    Bing 搜索结果页面 HTML 抓取 — 免 API Key
    国内服务器通常可以正常访问 www.bing.com / cn.bing.com
    多重选择器 fallback，适应 Bing 不同页面变体。
    """
    from bs4 import BeautifulSoup
    import time

    # 尝试两个域名
    domains = ['https://www.bing.com/search', 'https://cn.bing.com/search']
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    }
    params = {
        'q': query,
        'count': min(num_results, 15),
        'setlang': 'zh-Hans',
    }

    for domain in domains:
        try:
            resp = _http_get(domain, params=params, headers=headers, timeout=10)
            if not resp:
                print(f"[search_client] bing_html: no response from {domain}")
                continue
            if resp.status_code != 200:
                print(f"[search_client] bing_html: HTTP {resp.status_code} from {domain}")
                continue

            resp.encoding = 'utf-8'
            soup = BeautifulSoup(resp.text, 'lxml')
            results = []

            # 策略 1: 标准 .b_algo 选择器
            for item in soup.select('.b_algo'):
                title_tag = item.select_one('h2 a')
                if not title_tag:
                    continue
                title = title_tag.get_text(strip=True)
                link = title_tag.get('href', '')
                snippet_el = item.select_one('.b_caption p')
                snippet = snippet_el.get_text(strip=True) if snippet_el else ''
                if title and link:
                    results.append({'title': title, 'url': link, 'snippet': snippet})
                    if len(results) >= num_results:
                        break

            # 策略 2: 如果策略1失败，尝试更宽松的选择器
            if not results:
                print(f"[search_client] bing_html: standard .b_algo failed, trying fallback selectors")
                for item in soup.select('li') or soup.select('.b_algo'):
                    a = item.select_one('a') if hasattr(item, 'select_one') else None
                    if not a:
                        continue
                    title = a.get_text(strip=True)
                    link = a.get('href', '')
                    if title and link and link.startswith('http'):
                        # 找附近文本
                        snippet = ''
                        p = item.select_one('p')
                        if p:
                            snippet = p.get_text(strip=True)
                        else:
                            # 尝试找相邻文本节点
                            for sibling in a.parent.next_siblings if a.parent else []:
                                if hasattr(sibling, 'get_text'):
                                    text = sibling.get_text(strip=True)
                                    if text and len(text) > 10:
                                        snippet = text
                                        break
                        results.append({'title': title, 'url': link, 'snippet': snippet})
                        if len(results) >= num_results:
                            break

            if results:
                print(f"[search_client] bing_html({domain}) OK: {len(results)} results for query: {query[:30]}...")
                return results
            else:
                print(f"[search_client] bing_html({domain}): parsed 0 results for query: {query[:30]}...")

        except Exception as e:
            print(f"[search_client] bing_html({domain}) error: {e}")
            continue

    print(f"[search_client] bing_html: all domains failed for query: {query[:30]}...")
    return []


# ========================================
# SerpAPI — 需 API Key
# ========================================

def _search_serpapi(query, api_key, num_results=10):
    """SerpAPI 搜索"""
    url = 'https://serpapi.com/search'
    params = {
        'q': query,
        'api_key': api_key,
        'engine': 'google',
        'num': num_results,
    }
    resp = _http_get(url, params=params, timeout=8, data=None)
    if not resp or resp.status_code != 200:
        return []
    resp.encoding = 'utf-8'
    data = resp.json()
    results = []
    for item in data.get('organic_results', [])[:num_results]:
        results.append({
            'title': item.get('title', ''),
            'url': item.get('link', ''),
            'snippet': item.get('snippet', ''),
        })
    return results


# ========================================
# Bing API v7 — 需 API Key
# ========================================

def _search_bing_api(query, api_key, num_results=10):
    """Bing Web Search API v7"""
    url = 'https://api.bing.microsoft.com/v7.0/search'
    headers = {'Ocp-Apim-Subscription-Key': api_key}
    params = {'q': query, 'count': num_results, 'mkt': 'zh-CN'}
    resp = _http_get(url, params=params, headers=headers, timeout=8, data=None)
    if not resp or resp.status_code != 200:
        return []
    resp.encoding = 'utf-8'
    data = resp.json()
    results = []
    for item in data.get('webPages', {}).get('value', [])[:num_results]:
        results.append({
            'title': item.get('name', ''),
            'url': item.get('url', ''),
            'snippet': item.get('snippet', ''),
        })
    return results


# ========================================
# Tavily Search API — AI 专用搜索引擎
# ========================================
# Tavily 专为 AI Agent 设计，返回高质量结构化的搜索结果，
# 包含完整正文内容（非摘要片段）和相关性评分。
# 文档: https://docs.tavily.com

TAVILY_API_BASE = "https://api.tavily.com/search"


def _search_tavily(query, api_key, num_results=10):
    """
    Tavily 搜索 — AI 专用搜索引擎。

    返回格式统一为 [{title, url, snippet}]，
    其中 snippet 字段包含 Tavily 返回的完整 content（远优于传统搜索引擎摘要）。
    """
    import requests

    headers = {
        'Content-Type': 'application/json',
    }
    payload = {
        'api_key': api_key,
        'query': query,
        'search_depth': 'advanced',
        'include_answer': False,
        'include_raw_content': False,
        'max_results': min(num_results, 20),
    }

    proxies = _get_proxies()

    try:
        resp = requests.post(
            TAVILY_API_BASE,
            headers=headers,
            json=payload,
            timeout=15,
            proxies=proxies,
        )

        if resp.status_code != 200:
            print(f"[search_client] tavily: HTTP {resp.status_code}: {resp.text[:200]}")
            return []

        data = resp.json()
        results = []
        for item in data.get('results', [])[:num_results]:
            results.append({
                'title': item.get('title', ''),
                'url': item.get('url', ''),
                'snippet': item.get('content', ''),  # Tavily content 比传统 snippet 丰富得多
                'score': item.get('score', 0),       # 相关性评分
            })

        print(f"[search_client] tavily OK: {len(results)} results, response_time={data.get('response_time', '?')}s")
        return results

    except requests.exceptions.Timeout:
        print(f"[search_client] tavily: timeout for query: {query[:30]}...")
        return []
    except Exception as e:
        print(f"[search_client] tavily error: {e}")
        return []
