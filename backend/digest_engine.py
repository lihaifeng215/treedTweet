"""
每日热点日报引擎 v1.0

基于"纯代码生成，无大模型参与"的设计理念：
- 所有精选、分类、评分已在热点入库时完成
- 日报生成仅需代码按类型分桶 + 按分数排序
- 生成速度：毫秒级

日报结构：
  1. 技术发布/更新
  2. 产品发布/更新
  3. 行业动态
  4. 论文研究
  5. 技巧与观点

每个板块展示 TOP 3-5 条精选热点，含标题 + 摘要 + 来源 + 评分。
"""

import json
import os
import sqlite3
import time
import logging
from datetime import datetime, timedelta
from threading import Lock
from typing import Optional

from scoring_engine import (
    CATEGORY_BUCKETS, 
    SELECTION_THRESHOLDS,
    get_source_tier,
    get_scoring_stats,
)

logger = logging.getLogger(__name__)

# 日报数据库路径
DIGEST_DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'digests.db')
_lock = Lock()
_conn = None


def _get_conn():
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DIGEST_DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute('PRAGMA journal_mode=WAL')
    return _conn


def init_digest_db():
    """初始化日报数据库"""
    conn = _get_conn()
    with _lock:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS daily_digests (
                id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                content TEXT NOT NULL,
                stats TEXT,
                hotspot_ids TEXT,
                created_at TEXT NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_digest_date ON daily_digests(date DESC)')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS digest_hotspots (
                id TEXT PRIMARY KEY,
                digest_id TEXT NOT NULL,
                hotspot_slug TEXT NOT NULL,
                bucket TEXT NOT NULL,
                score REAL,
                created_at TEXT,
                FOREIGN KEY (digest_id) REFERENCES daily_digests(id)
            )
        ''')
        conn.commit()


def generate_daily_digest(hotspots: list[dict], date_str: str = None) -> dict:
    """
    纯代码生成每日热点日报
    
    设计原则：
      1. 不调用任何 LLM，纯代码逻辑
      2. 按桶分类 + 按评分降序
      3. 每个桶取 TOP 5
      4. 生成格式化的日报内容
    
    Args:
        hotspots: 已评分的热点列表（含 scoring + bucket 字段）
        date_str: 日报日期，默认今天
    
    Returns:
        {
            "id": "digest_xxx",
            "date": "2026-07-08",
            "title": "🔥 今日热点日报 · 2026年7月8日",
            "sections": [
                {
                    "id": "tech_launch",
                    "name": "技术发布/更新",
                    "icon": "🚀",
                    "items": [...],
                    "count": 3
                },
                ...
            ],
            "stats": {...},
            "created_at": "2026-07-08T08:00:00",
        }
    """
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    # 只保留精选热点
    selected = [
        h for h in hotspots 
        if h.get('scoring', {}).get('is_selected', False)
    ]
    
    # 按桶分组
    buckets = {}
    for h in selected:
        bucket = h.get('bucket', 'social_hot')
        if bucket not in buckets:
            buckets[bucket] = []
        buckets[bucket].append(h)
    
    # 每个桶按分数降序排列
    for bucket in buckets.values():
        bucket.sort(key=lambda x: x.get('scoring', {}).get('final_score', 0), reverse=True)
    
    # 构建日报板块
    sections = []
    bucket_order = ['tech_launch', 'product_launch', 'industry_trends', 
                    'research_paper', 'tips_opinion', 'social_hot']
    
    total_items = 0
    for bucket_id in bucket_order:
        items = buckets.get(bucket_id, [])
        if not items:
            continue
        
        bucket_info = CATEGORY_BUCKETS.get(bucket_id, {
            'name': '社会热点', 'icon': '🔥'
        })
        
        top_items = items[:5]  # 每个桶最多 5 条
        total_items += len(top_items)
        
        sections.append({
            'id': bucket_id,
            'name': bucket_info['name'],
            'icon': bucket_info['icon'],
            'items': [_format_digest_item(h) for h in top_items],
            'count': len(top_items),
        })
    
    # 生成日报标题
    weekday_names = ['一', '二', '三', '四', '五', '六', '日']
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    weekday = weekday_names[dt.weekday()]
    title = f"🔥 今日热点日报 · {dt.month}月{dt.day}日 周{weekday}"
    
    # 统计
    stats = get_scoring_stats(hotspots)
    stats['digest_items'] = total_items
    stats['sections_count'] = len(sections)
    
    digest_id = f"digest_{date_str}"
    created_at = datetime.now().isoformat()
    
    digest = {
        'id': digest_id,
        'date': date_str,
        'title': title,
        'sections': sections,
        'stats': {
            'total_hotspots': stats['total'],
            'selected_count': stats['selected'],
            'selection_rate': stats['selection_rate'],
            'avg_score': stats['avg_score'],
            'digest_items': total_items,
            'sections_count': len(sections),
            'tier_distribution': stats['tier_distribution'],
            'bucket_distribution': stats['bucket_distribution'],
        },
        'created_at': created_at,
    }
    
    # 持久化到数据库
    _save_digest(digest, selected)
    
    return digest


def _format_digest_item(hotspot: dict) -> dict:
    """格式化日报中的单条热点"""
    scoring = hotspot.get('scoring', {})
    tier_info = get_source_tier(hotspot.get('source_key', ''))
    
    return {
        'title': hotspot.get('title', ''),
        'url': hotspot.get('url', ''),
        'desc': (hotspot.get('desc', '') or '')[:200],
        'source': hotspot.get('source', ''),
        'source_icon': _get_source_icon(hotspot.get('source_key', '')),
        'source_tier': tier_info['tier'],
        'source_tier_label': _get_tier_label(tier_info['tier']),
        'score': scoring.get('final_score', 0),
        'dimension_scores': scoring.get('dimension_scores', {}),
        'engagement': hotspot.get('engagement', 0),
    }


def _get_source_icon(source_key: str) -> str:
    """获取信源图标"""
    icons = {
        'baidu': '🔍', 'douyin': '🎵', 'toutiao': '📰', 'weibo': '📢',
        'zhihu': '💡', 'hackernews': '🟠', 'github': '🐙', 'sspai': '📱',
        '36kr': '🦪', 'qbit': '🧠', 'bilibili': '🅱️', 'netease': '📰',
    }
    return icons.get(source_key, '📡')


def _get_tier_label(tier: str) -> str:
    """获取信源等级标签"""
    labels = {
        'T1': '⭐ 一手信源',
        'T1.5': '📋 官方平台',
        'T2': '📝 综合媒体',
    }
    return labels.get(tier, '📝')


def _save_digest(digest: dict, hotspots: list[dict]):
    """保存日报到数据库"""
    init_digest_db()
    conn = _get_conn()
    hotspot_ids = json.dumps([h.get('slug', '') for h in hotspots], ensure_ascii=False)
    
    with _lock:
        # 先删除当天的旧日报
        conn.execute('DELETE FROM daily_digests WHERE date=?', (digest['date'],))
        conn.execute('DELETE FROM digest_hotspots WHERE digest_id=?', (digest['id'],))
        
        # 插入新日报
        conn.execute(
            'INSERT INTO daily_digests (id, date, content, stats, hotspot_ids, created_at) '
            'VALUES (?, ?, ?, ?, ?, ?)',
            (
                digest['id'],
                digest['date'],
                json.dumps(digest, ensure_ascii=False),
                json.dumps(digest['stats'], ensure_ascii=False),
                hotspot_ids,
                digest['created_at'],
            )
        )
        
        # 插入热点关联
        now = datetime.now().isoformat()
        for h in hotspots:
            scoring = h.get('scoring', {})
            conn.execute(
                'INSERT OR REPLACE INTO digest_hotspots (id, digest_id, hotspot_slug, bucket, score, created_at) '
                'VALUES (?, ?, ?, ?, ?, ?)',
                (
                    f"{digest['id']}_{h.get('slug', '')}",
                    digest['id'],
                    h.get('slug', ''),
                    h.get('bucket', 'social_hot'),
                    scoring.get('final_score', 0),
                    now,
                )
            )
        
        conn.commit()


def get_latest_digest() -> Optional[dict]:
    """获取最新的日报"""
    init_digest_db()
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            'SELECT * FROM daily_digests ORDER BY date DESC LIMIT 1'
        ).fetchone()
    
    if row is None:
        return None
    
    return json.loads(row['content'])


def get_digest_by_date(date_str: str) -> Optional[dict]:
    """获取指定日期的日报"""
    init_digest_db()
    conn = _get_conn()
    with _lock:
        row = conn.execute(
            'SELECT * FROM daily_digests WHERE date=?',
            (date_str,)
        ).fetchone()
    
    if row is None:
        return None
    
    return json.loads(row['content'])


def list_digests(limit: int = 7) -> list[dict]:
    """获取最近的日报列表（仅摘要）"""
    init_digest_db()
    conn = _get_conn()
    with _lock:
        rows = conn.execute(
            'SELECT id, date, stats, created_at FROM daily_digests ORDER BY date DESC LIMIT ?',
            (limit,)
        ).fetchall()
    
    result = []
    for row in rows:
        stats = json.loads(row['stats']) if row['stats'] else {}
        result.append({
            'id': row['id'],
            'date': row['date'],
            'stats': stats,
            'created_at': row['created_at'],
        })
    return result


def delete_digest(digest_id: str):
    """删除日报"""
    init_digest_db()
    conn = _get_conn()
    with _lock:
        conn.execute('DELETE FROM daily_digests WHERE id=?', (digest_id,))
        conn.execute('DELETE FROM digest_hotspots WHERE digest_id=?', (digest_id,))
        conn.commit()


def generate_digest_markdown(digest: dict) -> str:
    """将日报转换为 Markdown 格式（方便导出/分享）"""
    lines = []
    lines.append(f"# {digest['title']}")
    lines.append('')
    lines.append(f"> 精选 {digest['stats']['digest_items']} 条热点 · "
                 f"来自 {digest['stats']['sections_count']} 个板块 · "
                 f"平均质量分 {digest['stats']['avg_score']}")
    lines.append('')
    
    for section in digest.get('sections', []):
        lines.append(f"## {section['icon']} {section['name']}（{section['count']}条）")
        lines.append('')
        for item in section.get('items', []):
            tier_badge = {
                'T1': '⭐', 'T1.5': '📋', 'T2': '📝'
            }.get(item.get('source_tier', 'T2'), '')
            score = item.get('score', 0)
            score_bar = '█' * min(int(score / 10), 10) + '░' * (10 - min(int(score / 10), 10))
            lines.append(f"### {item['title']}")
            lines.append(f"**{item['source_icon']} {item['source']}** {tier_badge} · "
                        f"质量分：{score}/100")
            lines.append(f"`{score_bar}` {score}分")
            if item.get('desc'):
                lines.append(f"> {item['desc']}")
            if item.get('url'):
                lines.append(f"🔗 [{item['url']}]({item['url']})")
            lines.append('')
    
    lines.append('---')
    created = digest.get('created_at', '')
    lines.append(f"*日报生成时间：{created} · 由 TrendArticle 自动生成*")
    
    return '\n'.join(lines)


# 初始化数据库
try:
    init_digest_db()
except Exception as e:
    logger.warning(f"Digest DB init failed: {e}")
