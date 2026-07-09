"""
Firecrawl 网页抓取客户端模块

Firecrawl 是一个专业的网页抓取/爬取服务，能将任意网页转换为
结构化的 Markdown 内容，适用于素材采集场景。

使用方式：
    from firecrawl_client import scrape_url
    ok, result = scrape_url("https://example.com")
"""

import json
import time
import logging
from config import get_config

logger = logging.getLogger(__name__)

FIRECRAWL_API_BASE = "https://api.firecrawl.dev/v1"
DEFAULT_TIMEOUT = 30  # 抓取超时（秒）


def scrape_url(url, formats=None, timeout=DEFAULT_TIMEOUT):
    """
    使用 Firecrawl 抓取指定 URL，返回 Markdown 格式内容。

    Args:
        url: 要抓取的网页 URL
        formats: 输出格式列表，默认 ["markdown"]
        timeout: 请求超时时间（秒）

    Returns:
        (ok: bool, result: dict|str)
        成功时 result = {
            'title': str,       # 网页标题
            'markdown': str,    # Markdown 格式正文
            'url': str,         # 最终 URL（可能被重定向）
            'status_code': int, # HTTP 状态码
            'credits_used': int # 消耗的 credits
        }
        失败时 result 为错误消息字符串
    """
    import requests

    cfg = get_config()
    api_key = cfg.get('firecrawl_api_key', '')
    if not api_key:
        return False, 'Firecrawl API Key 未配置，请在「设置」页面填写'

    if formats is None:
        formats = ["markdown"]

    headers = {
        'Authorization': f'Bearer {api_key}',
        'Content-Type': 'application/json',
    }

    payload = {
        'url': url,
        'formats': formats,
    }

    proxies = None
    proxy_url = cfg.get('proxy', '')
    if proxy_url:
        proxies = {'http': proxy_url, 'https': proxy_url}

    for attempt in range(2):
        try:
            resp = requests.post(
                f'{FIRECRAWL_API_BASE}/scrape',
                headers=headers,
                json=payload,
                timeout=timeout,
                proxies=proxies,
            )

            if resp.status_code == 402:
                return False, 'Firecrawl 配额不足，请检查账户余额'
            elif resp.status_code == 401:
                return False, 'Firecrawl API Key 无效'
            elif resp.status_code == 429:
                if attempt == 0:
                    time.sleep(1)
                    continue
                return False, 'Firecrawl 请求过于频繁，请稍后重试'
            elif resp.status_code != 200:
                err_msg = f'Firecrawl 返回错误 HTTP {resp.status_code}'
                try:
                    body = resp.json()
                    err_msg += f': {body.get("error", body.get("message", ""))}'
                except Exception:
                    err_msg += f': {resp.text[:200]}'
                return False, err_msg

            data = resp.json()
            if not data.get('success'):
                return False, data.get('error', 'Firecrawl 抓取失败')

            scrape_data = data.get('data', {})
            metadata = scrape_data.get('metadata', {})

            result = {
                'title': metadata.get('title', ''),
                'markdown': scrape_data.get('markdown', ''),
                'url': metadata.get('url', url),
                'status_code': metadata.get('statusCode', 0),
                'credits_used': metadata.get('creditsUsed', 1),
            }

            logger.info(
                f'[firecrawl] scraped "{result["title"]}" ({len(result["markdown"])} chars, '
                f'{result["credits_used"]} credits)'
            )
            return True, result

        except requests.exceptions.Timeout:
            if attempt == 0:
                logger.warning(f'[firecrawl] timeout, retrying... ({url[:50]})')
                continue
            return False, f'Firecrawl 请求超时（{timeout}s），目标网站可能响应过慢'
        except requests.exceptions.ConnectionError as e:
            if attempt == 0:
                time.sleep(0.5)
                continue
            return False, f'无法连接到 Firecrawl 服务: {str(e)[:100]}'
        except Exception as e:
            return False, f'Firecrawl 请求异常: {str(e)[:200]}'

    return False, 'Firecrawl 请求失败（已重试）'


def get_credit_usage():
    """
    查询 Firecrawl 账户配额使用情况。

    Returns:
        (ok: bool, result: dict|str)
        成功时 result = {
            'remaining_credits': int,
            'plan_credits': int,
            'billing_period_start': str,
            'billing_period_end': str,
        }
    """
    import requests

    cfg = get_config()
    api_key = cfg.get('firecrawl_api_key', '')
    if not api_key:
        return False, 'Firecrawl API Key 未配置'

    headers = {'Authorization': f'Bearer {api_key}'}

    proxies = None
    proxy_url = cfg.get('proxy', '')
    if proxy_url:
        proxies = {'http': proxy_url, 'https': proxy_url}

    try:
        resp = requests.get(
            f'{FIRECRAWL_API_BASE}/team/credit-usage',
            headers=headers,
            timeout=10,
            proxies=proxies,
        )
        if resp.status_code != 200:
            return False, f'Firecrawl 返回 HTTP {resp.status_code}'

        data = resp.json()
        if not data.get('success'):
            return False, data.get('error', '查询配额失败')

        usage = data.get('data', {})
        return True, {
            'remaining_credits': usage.get('remaining_credits', 0),
            'plan_credits': usage.get('plan_credits', 0),
            'billing_period_start': usage.get('billing_period_start', ''),
            'billing_period_end': usage.get('billing_period_end', ''),
        }

    except Exception as e:
        return False, f'查询配额失败: {str(e)[:100]}'
