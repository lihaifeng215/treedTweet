"""
信源分级与多维评分引擎 v1.0

基于"代码做判断，模型做评分"的核心设计理念：
- LLM 仅负责输出五维评分（不判断结果）
- 代码端用公式重新计算权重 + 质量分
- 代码端根据不同类别的精选阈值判断是否精选

信源分级（影响精选权重）：
  T1  - 官方一手信息，权重最高（2.0x）: GitHub、Hacker News
  T1.5 - 官方社交媒体/平台官方数据（1.5x）: 百度热搜、微博热搜、知乎热榜
  T2  - KOL/个人/媒体/综合资讯（1.0x）: 36氪、少数派、B站、抖音、头条、网易、量子位

五维评分维度（由 LLM 输出）：
  1. freshness     - 新鲜度（0-100）：事件的新近程度
  2. authority     - 权威性（0-100）：信源/内容的可信度
  3. relevance     - 相关性（0-100）：与目标受众的相关度
  4. social_impact - 传播力（0-100）：社交传播潜力
  5. uniqueness    - 独特性（0-100）：内容差异化程度

最终质量分公式 = (
    freshness * 0.15 + authority * 0.25 + relevance * 0.30 + 
    social_impact * 0.20 + uniqueness * 0.10
) * source_tier_weight

精选阈值：
  T1 信源：总分 >= 55 即可精选
  T1.5 信源：总分 >= 62 即可精选
  T2 信源：总分 >= 68 即可精选
"""

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ============================================================
# 信源分级配置
# ============================================================

# T1: 官方一手信息 — 权重 2.0x
SOURCE_TIER_T1 = {
    'github': 2.0,      # GitHub Trending — 官方开源平台
    'hackernews': 2.0,   # Hacker News — 顶级技术社区
}

# T1.5: 官方社交媒体/平台官方数据 — 权重 1.5x
SOURCE_TIER_T1_5 = {
    'baidu': 1.5,        # 百度热搜 — 国内最大搜索引擎官方数据
    'weibo': 1.5,        # 微博热搜 — 最大的社交媒体热搜
    'zhihu': 1.5,        # 知乎热榜 — 高质量问答社区数据
}

# T2: KOL/个人/媒体/综合资讯 — 权重 1.0x
SOURCE_TIER_T2 = {
    'douyin': 1.0,       # 抖音热榜 — UGC 平台
    'toutiao': 1.0,      # 头条热榜 — 算法推荐平台
    'sspai': 1.0,        # 少数派 — 科技媒体
    '36kr': 1.0,         # 36氪 — 科技媒体
    'qbit': 1.0,         # 量子位 — AI 媒体
    'bilibili': 1.0,     # B站热门 — UGC 视频平台
    'netease': 1.0,      # 网易新闻 — 新闻门户
}

# 合并为统一查询字典
SOURCE_TIERS = {}
SOURCE_TIERS.update({k: {'tier': 'T1', 'weight': v} for k, v in SOURCE_TIER_T1.items()})
SOURCE_TIERS.update({k: {'tier': 'T1.5', 'weight': v} for k, v in SOURCE_TIER_T1_5.items()})
SOURCE_TIERS.update({k: {'tier': 'T2', 'weight': v} for k, v in SOURCE_TIER_T2.items()})

# 精选阈值：不同信源层级的精选门槛
SELECTION_THRESHOLDS = {
    'T1': 55,   # 官方一手信息：55分即可精选
    'T1.5': 62, # 官方平台数据：62分
    'T2': 68,   # 媒体/KOL：需要68分
}

# 默认权重（未知信源）
DEFAULT_TIER = {'tier': 'T2', 'weight': 1.0}

# ============================================================
# 分类映射：用于日报分桶
# ============================================================

CATEGORY_BUCKETS = {
    'tech_launch': {
        'name': '技术发布/更新',
        'icon': '🚀',
        'keywords': ['开源', '发布', '更新', 'release', '推出', '上线', '新功能', '模型', 'API',
                      'GitHub', 'github', '版本', '升级', '开箱', '试用', '体验'],
    },
    'product_launch': {
        'name': '产品发布/更新',
        'icon': '📱',
        'keywords': ['产品', '发布', 'APP', 'app', '应用', '硬件', '设备', '测试', '上线',
                      '上市', '发布', '推出', '新品', '版本更新'],
    },
    'industry_trends': {
        'name': '行业动态',
        'icon': '📊',
        'keywords': ['融资', '投资', '收购', '上市', '裁员', '招聘', '合作', '战略', '布局',
                      '趋势', '报告', '数据', '财报', '估值', '赛道', '风口'],
    },
    'research_paper': {
        'name': '论文研究',
        'icon': '🔬',
        'keywords': ['论文', '研究', 'paper', 'arxiv', '实验', '基准', 'benchmark', 'SOTA',
                      '算法', '架构', '训练', '微调', '学术'],
    },
    'tips_opinion': {
        'name': '技巧与观点',
        'icon': '💡',
        'keywords': ['技巧', '教程', '指南', '观点', '思考', '经验', '方法论', '总结', '复盘',
                      '干货', '分享', '心得', '实践', '最佳实践', '设计', '用户体验'],
    },
    'social_hot': {
        'name': '社会热点',
        'icon': '🔥',
        'keywords': [],  # 兜底分类
    },
}

# ============================================================
# 五维评分公式（纯代码计算）
# ============================================================

# 评分维度权重
DIMENSION_WEIGHTS = {
    'freshness': 0.15,      # 新鲜度
    'authority': 0.25,      # 权威性（最重要）
    'relevance': 0.30,      # 相关性（最重要）
    'social_impact': 0.20,  # 传播力
    'uniqueness': 0.10,     # 独特性
}

# 五维评分 Prompt（精简版，约 50 行）
SCORING_SYSTEM_PROMPT = """你是一位热点信息质量评估专家。请对给定热点进行五维评分，每个维度 0-100 分。
评分标准：

1. freshness（新鲜度）：事件的新近程度
   - 90-100：过去1小时内发生
   - 70-89：今天发生
   - 50-69：本周内
   - 0-49：更早

2. authority（权威性）：信源/内容的可信度
   - 90-100：官方一手公告/论文
   - 70-89：知名科技媒体/大V独家
   - 50-69：普通媒体报道
   - 0-49：来源不明/推测

3. relevance（相关性）：内容创作价值
   - 90-100：极具话题性，自媒体必做
   - 70-89：有明确创作切入点
   - 50-69：可做但不紧迫
   - 0-49：话题性弱

4. social_impact（传播力）：社交传播潜力
   - 90-100：全民关注/破圈话题
   - 70-89：行业热议
   - 50-69：小范围讨论
   - 0-49：关注度低

5. uniqueness（独特性）：内容差异化程度
   - 90-100：独家/首发/独特视角
   - 70-89：角度新颖
   - 50-69：有一定差异化
   - 0-49：同质化严重

输出 JSON 格式，只输出 JSON：
{"freshness": 0, "authority": 0, "relevance": 0, "social_impact": 0, "uniqueness": 0, "reason": "一句话评分理由"}"""


def get_source_tier(source_key: str) -> dict:
    """获取信源的分级和权重"""
    return SOURCE_TIERS.get(source_key, DEFAULT_TIER)


def calculate_quality_score(scores: dict, source_key: str) -> dict:
    """
    纯代码计算最终质量分（LLM 不参与）
    
    Args:
        scores: LLM 输出的五维评分 {"freshness": 85, "authority": 70, ...}
        source_key: 信源标识
    
    Returns:
        {
            "final_score": 68.5,           # 最终质量分（0-100）
            "raw_score": 62.3,             # 原始加权分（不含信源权重）
            "source_tier": "T2",           # 信源等级
            "source_weight": 1.0,          # 信源权重
            "is_selected": False,          # 是否精选
            "selected_threshold": 68,      # 该层级精选线
            "dimension_scores": {...},     # 各维度得分
            "dimension_weights": {...},    # 各维度权重
        }
    """
    tier_info = get_source_tier(source_key)
    source_weight = tier_info['weight']
    tier = tier_info['tier']
    
    # 计算原始加权分
    raw_score = sum(
        scores.get(dim, 0) * weight
        for dim, weight in DIMENSION_WEIGHTS.items()
    )
    
    # 应用信源权重
    final_score = round(raw_score * source_weight, 1)
    
    # 判断精选
    threshold = SELECTION_THRESHOLDS.get(tier, 68)
    is_selected = final_score >= threshold
    
    return {
        'final_score': min(final_score, 100),
        'raw_score': round(raw_score, 1),
        'source_tier': tier,
        'source_weight': source_weight,
        'is_selected': is_selected,
        'selected_threshold': threshold,
        'dimension_scores': scores,
        'dimension_weights': DIMENSION_WEIGHTS,
    }


def classify_to_bucket(title: str, desc: str = '', source: str = '') -> str:
    """
    将热点分到日报桶（纯代码，无 LLM 参与）
    按关键词匹配进行分桶
    
    Returns:
        桶标识: 'tech_launch' | 'product_launch' | 'industry_trends' | 
                 'research_paper' | 'tips_opinion' | 'social_hot'
    """
    text = (title + ' ' + desc + ' ' + source).lower()
    
    # 按优先级匹配
    for bucket_id, bucket_info in CATEGORY_BUCKETS.items():
        if bucket_id == 'social_hot':
            continue  # 兜底，最后匹配
        for kw in bucket_info['keywords']:
            if kw.lower() in text:
                return bucket_id
    
    return 'social_hot'


def score_batch(hotspots: list[dict], llm_score_func) -> list[dict]:
    """
    批量评分热点（LLM评分 + 代码公式决策）
    
    Args:
        hotspots: 热点列表
        llm_score_func: LLM 评分函数，签名为 func(hotspot) -> dict or None
    
    Returns:
        已评分的热点列表（新增 scoring 字段）
    """
    scored = []
    for hs in hotspots:
        source_key = hs.get('source_key', '')
        
        try:
            # 调用 LLM 获取五维评分
            scores = llm_score_func(hs)
            if scores and isinstance(scores, dict):
                # 代码端计算最终质量分
                quality = calculate_quality_score(scores, source_key)
            else:
                quality = _default_scores(source_key)
        except Exception as e:
            logger.warning(f"Scoring failed for {hs.get('title', '')[:30]}: {e}")
            quality = _default_scores(source_key)
        
        # 自动分桶
        bucket = classify_to_bucket(
            hs.get('title', ''), 
            hs.get('desc', ''),
            hs.get('source', '')
        )
        
        hs['scoring'] = quality
        hs['bucket'] = bucket
        hs['bucket_name'] = CATEGORY_BUCKETS.get(bucket, {}).get('name', '社会热点')
        hs['bucket_icon'] = CATEGORY_BUCKETS.get(bucket, {}).get('icon', '🔥')
        
        scored.append(hs)
    
    # 精选排序：先按是否精选，再按分数降序
    scored.sort(key=lambda x: (
        not x['scoring']['is_selected'],
        -x['scoring']['final_score']
    ))
    
    return scored


def _default_scores(source_key: str) -> dict:
    """默认评分（LLM 评分失败时）"""
    return {
        'final_score': 50.0,
        'raw_score': 50.0,
        'source_tier': get_source_tier(source_key)['tier'],
        'source_weight': get_source_tier(source_key)['weight'],
        'is_selected': False,
        'selected_threshold': SELECTION_THRESHOLDS.get(
            get_source_tier(source_key)['tier'], 68
        ),
        'dimension_scores': {
            'freshness': 50,
            'authority': 50,
            'relevance': 50,
            'social_impact': 50,
            'uniqueness': 50,
        },
        'dimension_weights': DIMENSION_WEIGHTS,
    }


def get_scoring_stats(hotspots: list[dict]) -> dict:
    """获取评分统计"""
    selected = [h for h in hotspots if h.get('scoring', {}).get('is_selected')]
    tier_stats = {'T1': 0, 'T1.5': 0, 'T2': 0}
    bucket_stats = {}
    
    for h in hotspots:
        tier = h.get('scoring', {}).get('source_tier', 'T2')
        tier_stats[tier] = tier_stats.get(tier, 0) + 1
        
        bucket = h.get('bucket', 'social_hot')
        bucket_stats[bucket] = bucket_stats.get(bucket, 0) + 1
    
    avg_score = 0
    if hotspots:
        scores = [h.get('scoring', {}).get('final_score', 50) for h in hotspots]
        avg_score = round(sum(scores) / len(scores), 1)
    
    return {
        'total': len(hotspots),
        'selected': len(selected),
        'selection_rate': round(len(selected) / len(hotspots) * 100, 1) if hotspots else 0,
        'avg_score': avg_score,
        'tier_distribution': tier_stats,
        'bucket_distribution': bucket_stats,
        'top_selected': selected[:5],
    }
