/**
 * 素材库 · 热点列表交互逻辑
 * 公共工具函数已移至 common.js
 */

// 全局状态
let allHotspots = [];
let currentSourceFilter = 'all';
let currentCategoryFilter = 'all';
let currentTierFilter = 'all';
let currentSort = 'default';
let currentSearch = '';
let llmConfigured = false;
let sourceHealthData = {};
let lastRefreshTime = null;
let countdownTimer = null;
const CACHE_TTL = 300; // 5分钟缓存

// 信源分级配置（v6.1）
const SOURCE_TIERS = {
  'github': 'T1', 'hackernews': 'T1',
  'baidu': 'T1.5', 'weibo': 'T1.5', 'zhihu': 'T1.5',
  'douyin': 'T2', 'toutiao': 'T2', 'sspai': 'T2',
  '36kr': 'T2', 'qbit': 'T2', 'bilibili': 'T2', 'netease': 'T2',
};
const TIER_LABELS = { 'T1': '⭐ 一手信源', 'T1.5': '📋 官方平台', 'T2': '📝 综合媒体' };
const TIER_WEIGHTS = { 'T1': 2.0, 'T1.5': 1.5, 'T2': 1.0 };
const TIER_CLASSES = { 'T1': 't1', 'T1.5': 't1-5', 'T2': 't2' };

document.addEventListener('DOMContentLoaded', () => {
  loadHotspots(false);
  document.querySelectorAll('#sourceFilterTabs .filter-tab').forEach(tab => {
    tab.addEventListener('click', () => filterBySource(tab.dataset.filter));
  });
  document.querySelectorAll('#categoryFilterTabs .filter-tab').forEach(tab => {
    tab.addEventListener('click', () => filterByCategory(tab.dataset.filter));
  });
  document.querySelectorAll('#tierFilterTabs .filter-tab').forEach(tab => {
    tab.addEventListener('click', () => filterByTier(tab.dataset.filter));
  });
  // 键盘快捷键
  document.addEventListener('keydown', (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === 'k') {
      e.preventDefault();
      document.getElementById('searchInput').focus();
    }
  });
});

function renderSourceHealth(health) {
  sourceHealthData = health || {};
  document.querySelectorAll('.source-status-dot').forEach(dot => {
    const sourceKey = dot.dataset.source;
    if (!sourceKey) return;
    const h = health[sourceKey];
    dot.className = 'source-status-dot';
    if (!h || h.total_fetches === 0) dot.classList.add('unknown');
    else if (h.status === 'healthy') dot.classList.add('healthy');
    else if (h.status === 'degraded') dot.classList.add('degraded');
    else dot.classList.add('unhealthy');
  });
}

async function loadHotspots(forceRefresh) {
  const grid = document.getElementById('hotspotsGrid');
  grid.innerHTML = `<div class="loading-state"><div class="spinner"></div><p>${forceRefresh ? '正在刷新热点数据...' : '正在加载热点数据...'}</p></div>`;
  try {
    const url = forceRefresh ? '/api/hotspots?refresh=true' : '/api/hotspots';
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    allHotspots = data.hotspots.map((h, i) => ({ ...h, id: i }));
    llmConfigured = !!data.llm_configured;
    renderSourceHealth(data.source_health || {});
    document.getElementById('statTotal').textContent = allHotspots.length;
    const srcKeys = Object.keys(data.sources || {});
    document.getElementById('statSources').textContent = srcKeys.filter(k => (data.sources[k] || 0) > 0).length;
    document.getElementById('statTime').textContent = formatTime(data.generated_at);
    if (data.fetch_time_sec) document.getElementById('statTime').textContent += ` (${data.fetch_time_sec}s)`;
    lastRefreshTime = Date.now();
    startCountdown();
    applyFilters();
    if (forceRefresh) showToast('✅ 热点数据已更新', 'success');
    else if (data.cached) showToast('📋 已加载缓存热点 · 点击「刷新热点」获取最新', 'success');
  } catch (err) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon">⚠️</div><h3>加载失败</h3><p>${escapeHTML(err.message)} · 请检查网络连接</p><button class="btn btn-primary" onclick="refreshHotspots()">重试</button></div>`;
    showToast('❌ 加载热点数据失败', 'error');
  }
}

async function refreshHotspots() { await loadHotspots(true); }

function renderHotspots(items) {
  const grid = document.getElementById('hotspotsGrid');
  if (items.length === 0) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon">🔍</div><h3>暂无匹配数据</h3><p>尝试切换筛选条件或刷新数据</p></div>`;
    return;
  }
  grid.innerHTML = items.map((item, idx) => {
    const sourceClass = getSourceClass(item.source);
    const rank = idx + 1;
    const engagement = formatEngagement(item.engagement);
    const meta = getMetaHTML(item);
    const category = getContentType(item.source);
    const categoryName = getCategoryName(category);
    const categoryIcon = getCategoryIcon(category);
    const hotspotJson = escapeAttr(JSON.stringify(item));
    
    // v6.1: 信源分级标签
    const tier = getSourceTier(item);
    const tierLabel = TIER_LABELS[tier] || '';
    const tierClass = TIER_CLASSES[tier] || 't2';
    
    // 估算质量分（基于信源权重和热度）
    const estimatedScore = Math.min(99, Math.round(40 + TIER_WEIGHTS[tier] * 15 + Math.min(item.engagement || 0, 1000000) / 20000));
    const scoreClass = estimatedScore >= 75 ? 'high' : (estimatedScore >= 55 ? 'mid' : 'low');
    
    return `
      <div class="hotspot-card fade-in" data-id="${item.id}" data-source="${escapeHTML(item.source)}" style="animation-delay:${idx * 0.04}s">
        <div class="card-header">
          <span class="card-source ${sourceClass}">${getSourceIcon(item.source)} ${escapeHTML(item.source)}</span>
          <span class="card-rank">#${rank}</span>
        </div>
        <div class="card-title">${escapeHTML(item.title)}</div>
        ${item.desc || item.body ? `<div class="card-desc">${escapeHTML((item.desc || item.body || '').substring(0, 120))}</div>` : ''}
        <div class="card-meta">
          ${meta}
          <span>👁 ${engagement}</span>
          ${categoryName ? `<span class="card-category-badge">${categoryIcon} ${categoryName}</span>` : ''}
          <span class="tier-badge ${tierClass}">${tierLabel}</span>
          <span class="score-badge ${scoreClass}">${estimatedScore}分</span>
        </div>
        <div class="card-actions">
          <button class="card-action-btn primary" onclick="event.stopPropagation(); startWorkflow('${hotspotJson}')">
            ✨ 开始创作
          </button>
          <button class="card-action-btn" onclick="event.stopPropagation(); copyTitle('${escapeAttr(item.title)}')">
            📋 标题
          </button>
        </div>
      </div>`;
  }).join('');
}

function startWorkflow(hotspotJson) {
  if (!llmConfigured) {
    showToast('⚠️ 请先在「设置」中配置大模型 API', 'error');
    setTimeout(() => location.href = '/settings', 1200);
    return;
  }
  sessionStorage.setItem('pendingHotspot', hotspotJson);
  if (window.top !== window.self) {
    window.parent.postMessage({type:'navigate',target:'workflow'},'*');
  } else {
    location.href = '/';
  }
}

function getSourceTier(item) {
  // 先尝试 source_key，再尝试 source 映射
  const key = item.source_key || '';
  if (key && SOURCE_TIERS[key]) return SOURCE_TIERS[key];
  // 通过 source 名称反查
  const sourceMap = {
    'Hacker News': 'hackernews', 'GitHub Trending': 'github',
    '百度热搜': 'baidu', '微博热搜': 'weibo', '知乎热榜': 'zhihu',
    '抖音热榜': 'douyin', '头条热榜': 'toutiao', 'B站热门': 'bilibili',
    '36氪': '36kr', '少数派': 'sspai', '量子位': 'qbit', '网易新闻': 'netease',
  };
  const mappedKey = sourceMap[item.source] || '';
  return SOURCE_TIERS[mappedKey] || 'T2';
}

function getMetaHTML(item) {
  const parts = [];
  if (item.points) parts.push(`<span>⬆ ${item.points}</span>`);
  if (item.upvotes) parts.push(`<span>⬆ ${item.upvotes}</span>`);
  if (item.stars) parts.push(`<span>⭐ ${item.stars}</span>`);
  if (item.comments) parts.push(`<span>💬 ${item.comments}</span>`);
  if (item.author) parts.push(`<span>👤 ${escapeHTML(item.author)}</span>`);
  if (item.heat) parts.push(`<span>🔥 ${escapeHTML(String(item.heat))}</span>`);
  if (item.timestamp) parts.push(`<span>🕐 ${formatRelativeTime(item.timestamp)}</span>`);
  return parts.join('');
}

function filterBySource(source) {
  currentSourceFilter = source;
  document.querySelectorAll('#sourceFilterTabs .filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === source);
  });
  applyFilters();
}

function filterByCategory(category) {
  currentCategoryFilter = category;
  document.querySelectorAll('#categoryFilterTabs .filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === category);
  });
  applyFilters();
}

function filterByTier(tier) {
  currentTierFilter = tier;
  document.querySelectorAll('#tierFilterTabs .filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === tier);
  });
  applyFilters();
}

function applyFilters() {
  let filtered = allHotspots;
  // 搜索过滤
  if (currentSearch) {
    const q = currentSearch.toLowerCase();
    filtered = filtered.filter(h =>
      (h.title || '').toLowerCase().includes(q) ||
      (h.desc || h.body || '').toLowerCase().includes(q) ||
      (h.source || '').toLowerCase().includes(q)
    );
  }
  if (currentSourceFilter !== 'all') filtered = filtered.filter(h => h.source === currentSourceFilter);
  if (currentCategoryFilter !== 'all') filtered = filtered.filter(h => getContentType(h.source) === currentCategoryFilter);
  if (currentTierFilter !== 'all') filtered = filtered.filter(h => getSourceTier(h) === currentTierFilter);

  // 排序
  if (currentSort === 'engagement') {
    filtered = [...filtered].sort((a, b) => (b.engagement || 0) - (a.engagement || 0));
  } else if (currentSort === 'source') {
    filtered = [...filtered].sort((a, b) => (a.source || '').localeCompare(b.source || '', 'zh'));
  } else if (currentSort === 'title') {
    filtered = [...filtered].sort((a, b) => (a.title || '').localeCompare(b.title || '', 'zh'));
  }
  renderHotspots(filtered);
}

// ========================
// 搜索
// ========================

function onSearchInput() {
  const input = document.getElementById('searchInput');
  currentSearch = input.value.trim();
  const clearBtn = document.getElementById('searchClear');
  clearBtn.style.display = currentSearch ? 'inline-flex' : 'none';
  applyFilters();
}

function clearSearch() {
  const input = document.getElementById('searchInput');
  input.value = '';
  currentSearch = '';
  document.getElementById('searchClear').style.display = 'none';
  applyFilters();
  input.focus();
}

// ========================
// 排序
// ========================

function onSortChange() {
  currentSort = document.getElementById('sortSelect').value;
  applyFilters();
}

// ========================
// 自动刷新倒计时
// ========================

function startCountdown() {
  if (countdownTimer) clearInterval(countdownTimer);
  updateCountdown();
  countdownTimer = setInterval(updateCountdown, 10000); // 每10秒更新
}

function updateCountdown() {
  const el = document.getElementById('refreshCountdown');
  if (!lastRefreshTime) {
    el.textContent = '';
    return;
  }
  const elapsed = Math.floor((Date.now() - lastRefreshTime) / 1000);
  const remaining = Math.max(0, CACHE_TTL - elapsed);
  if (remaining <= 0) {
    el.textContent = '⏰ 缓存已过期';
    el.style.color = 'var(--accent-orange)';
  } else {
    const mins = Math.floor(remaining / 60);
    const secs = remaining % 60;
    el.textContent = `🔄 ${mins}:${String(secs).padStart(2, '0')} 后过期`;
    el.style.color = 'var(--text-muted)';
  }
}

// ========================
// 页面标签切换
// ========================

let currentTab = 'hotspots';

function switchTab(tab) {
  currentTab = tab;
  document.getElementById('tabHotspots').classList.toggle('active', tab === 'hotspots');
  document.getElementById('tabArticles').classList.toggle('active', tab === 'articles');
  document.getElementById('filtersSection').style.display = tab === 'hotspots' ? '' : 'none';
  document.getElementById('statsBar').style.display = tab === 'hotspots' ? '' : 'none';
  document.getElementById('hotspotsSection').style.display = tab === 'hotspots' ? '' : 'none';
  document.getElementById('articlesSection').style.display = tab === 'articles' ? '' : 'none';

  if (tab === 'articles') {
    loadSavedArticles();
  }
}

async function loadSavedArticles() {
  const grid = document.getElementById('articlesGrid');
  grid.innerHTML = '<div class="loading-state"><div class="spinner"></div><p>正在加载文章...</p></div>';

  try {
    const resp = await fetch('/api/articles?limit=50');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();
    const articles = data.articles || [];

    if (articles.length === 0) {
      grid.innerHTML = '<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon">📭</div><h3>暂无已保存的文章</h3><p>在工作流中完成创作并导出后，文章将自动保存在这里。</p></div>';
      return;
    }

    grid.innerHTML = articles.map((a, i) => `
      <div class="hotspot-card fade-in article-card" style="animation-delay:${i * 0.04}s" data-aid="${a.id}">
        <div class="card-header">
          <span class="card-source">📄 已保存</span>
          <span class="card-rank">${formatTime(a.created_at)}</span>
        </div>
        <div class="card-title">${escapeHTML(a.title || '无标题')}</div>
        ${a.hotspot_title ? `<div class="card-desc">基于：${escapeHTML(a.hotspot_title)}</div>` : ''}
        <div class="card-meta">
          ${a.style ? `<span>🎨 ${escapeHTML(a.style)}</span>` : ''}
          <span>📝 ${a.word_count || 0} 字</span>
        </div>
        <div class="card-actions">
          <button class="card-action-btn primary" onclick="event.stopPropagation(); viewArticle('${a.id}')">👁 查看</button>
          <button class="card-action-btn" onclick="event.stopPropagation(); deleteArticle('${a.id}')">🗑 删除</button>
        </div>
      </div>
    `).join('');
  } catch (e) {
    grid.innerHTML = `<div class="empty-state" style="grid-column:1/-1;"><div class="empty-icon">⚠️</div><h3>加载失败</h3><p>${escapeHTML(e.message)}</p></div>`;
  }
}

async function viewArticle(aid) {
  try {
    const resp = await fetch(`/api/articles/${aid}`);
    if (!resp.ok) throw new Error('文章不存在');
    const data = await resp.json();
    const a = data.article;
    // 在新窗口中预览
    const w = window.open('', '_blank');
    w.document.write(`<!DOCTYPE html><html lang="zh-CN"><head><meta charset="UTF-8"><title>${escapeHTML(a.title)}</title>
      <style>body{font-family:system-ui,sans-serif;max-width:800px;margin:40px auto;padding:20px;line-height:1.8;color:#333}
      h1{border-bottom:2px solid #eee;padding-bottom:12px}pre{background:#f5f5f5;padding:12px;border-radius:8px;white-space:pre-wrap}
      .meta{color:#888;font-size:14px;margin-bottom:20px}</style></head><body>
      <h1>${escapeHTML(a.title)}</h1>
      <div class="meta">${formatTime(a.created_at)} · ${a.word_count} 字</div>
      <div>${formatSavedContent(a.content)}</div></body></html>`);
  } catch (e) {
    showToast('加载文章失败：' + e.message, 'error');
  }
}

function formatSavedContent(text) {
  if (!text) return '';
  let html = escapeHTML(text);
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\n{2,}/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');
  return '<p>' + html + '</p>';
}

async function deleteArticle(aid) {
  if (!confirm('确定删除这篇文章吗？')) return;
  try {
    const resp = await fetch(`/api/articles/${aid}`, { method: 'DELETE' });
    if (!resp.ok) throw new Error('删除失败');
    showToast('文章已删除', 'success');
    if (currentTab === 'articles') loadSavedArticles();
  } catch (e) {
    showToast('删除失败：' + e.message, 'error');
  }
}
