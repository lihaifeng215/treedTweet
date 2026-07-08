/**
 * TrendTweet · 前端交互逻辑
 */

// 全局状态
let allHotspots = [];
let selectedHotspotId = null;
let currentSourceFilter = 'all';
let currentCategoryFilter = 'all';
let llmConfigured = false;
let isCached = false;
let sourceHealthData = {};

// 初始化：加载缓存数据（不强制刷新）
document.addEventListener('DOMContentLoaded', () => {
  loadHotspots(false);
});

// ========================
// 数据源健康状态（更新到筛选按钮的状态点）
// ========================

function renderSourceHealth(health) {
  sourceHealthData = health || {};

  // 更新筛选按钮上的状态点
  document.querySelectorAll('.source-status-dot').forEach(dot => {
    const sourceKey = dot.dataset.source;
    if (!sourceKey) return;
    const h = health[sourceKey];
    dot.className = 'source-status-dot';
    if (!h || h.total_fetches === 0) {
      dot.classList.add('unknown');
    } else if (h.status === 'healthy') {
      dot.classList.add('healthy');
    } else if (h.status === 'degraded') {
      dot.classList.add('degraded');
    } else {
      dot.classList.add('unhealthy');
    }
  });
}

// ========================
// 系统监控面板
// ========================

let monitorInterval = null;

function toggleMonitor() {
  const panel = document.getElementById('monitorPanel');
  if (panel.style.display === 'none' || !panel.style.display) {
    loadPerformance();
    panel.style.display = 'block';
    if (!monitorInterval) {
      monitorInterval = setInterval(loadPerformance, 30000);
    }
    panel.scrollIntoView({ behavior: 'smooth', block: 'start' });
  } else {
    panel.style.display = 'none';
    if (monitorInterval) {
      clearInterval(monitorInterval);
      monitorInterval = null;
    }
  }
}

async function loadPerformance() {
  try {
    const resp = await fetch('/api/performance');
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    document.getElementById('monCpu').textContent = data.cpu_percent + '%';
    document.getElementById('monCpuBar').style.width = Math.min(data.cpu_percent, 100) + '%';
    document.getElementById('monCpuBar').style.background = getBarColor(data.cpu_percent);

    document.getElementById('monMem').textContent = data.memory_percent + '%';
    document.getElementById('monMemBar').style.width = data.memory_percent + '%';
    document.getElementById('monMemBar').style.background = getBarColor(data.memory_percent);

    document.getElementById('monProcMem').textContent = data.process_rss_mb + ' MB';
    document.getElementById('monFetchTime').textContent = data.last_fetch_time_sec + ' s';
    document.getElementById('monCacheAge').textContent = data.cache_age > 0 ? Math.round(data.cache_age) + ' s' : '--';

    const uptime = data.uptime;
    const hours = Math.floor(uptime / 3600);
    const mins = Math.floor((uptime % 3600) / 60);
    document.getElementById('monUptime').textContent = hours + 'h ' + mins + 'm';
  } catch (err) {
    console.error('Failed to load performance:', err);
  }
}

function getBarColor(percent) {
  if (percent < 50) return 'var(--accent-green)';
  if (percent < 80) return 'var(--accent-orange)';
  return 'var(--accent-pink)';
}

// ========================
// 热点数据获取
// ========================

async function loadHotspots(forceRefresh) {
  const grid = document.getElementById('hotspotsGrid');
  grid.innerHTML = `
    <div class="loading-state">
      <div class="spinner"></div>
      <p>${forceRefresh ? '正在刷新热点数据...' : '正在加载热点数据...'}</p>
    </div>`;

  try {
    const url = forceRefresh ? '/api/hotspots?refresh=true' : '/api/hotspots';
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    const data = await resp.json();

    allHotspots = data.hotspots.map((h, i) => ({ ...h, id: i }));
    selectedHotspotId = null;
    llmConfigured = !!data.llm_configured;
    isCached = !!data.cached;
    sourceHealthData = data.source_health || {};

    // 更新统计
    document.getElementById('statTotal').textContent = allHotspots.length;
    const srcKeys = Object.keys(data.sources || {});
    document.getElementById('statSources').textContent = srcKeys.filter(k => (data.sources[k] || 0) > 0).length;
    document.getElementById('statTime').textContent = formatTime(data.generated_at);

    // 显示抓取耗时
    if (data.fetch_time_sec) {
      document.getElementById('statTime').textContent += ` (${data.fetch_time_sec}s)`;
    }

    // 渲染数据源健康状态
    renderSourceHealth(data.source_health || {});

    // 显示合成文章按钮
    const batchBtn = document.getElementById('batchBtn');
    if (batchBtn) {
      batchBtn.style.display = (llmConfigured && allHotspots.length > 0) ? '' : 'none';
    }

    // 渲染卡片
    applyFilters();

    // 隐藏空状态
    document.getElementById('emptyState').style.display = 'none';
    document.getElementById('tweetPanel').style.display = 'none';
    document.getElementById('analysisPanel').style.display = 'none';

    if (forceRefresh) {
      showToast('✅ 热点数据已更新', 'success');
    } else if (isCached) {
      showToast('📋 已加载缓存热点 · 点击「刷新热点」获取最新数据', 'success');
    }
  } catch (err) {
    console.error('Failed to fetch hotspots:', err);
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">⚠️</div>
        <h3>加载失败</h3>
        <p>${escapeHTML(err.message)} · 请检查网络连接或稍后重试</p>
        <button class="btn btn-primary" onclick="refreshHotspots()">重试</button>
      </div>`;
    showToast('❌ 加载热点数据失败', 'error');
  }
}

async function refreshHotspots() {
  await loadHotspots(true);
}

// ========================
// 渲染热点卡片
// ========================

function renderHotspots(items) {
  const grid = document.getElementById('hotspotsGrid');

  if (items.length === 0) {
    grid.innerHTML = `
      <div class="empty-state" style="grid-column:1/-1;">
        <div class="empty-icon">🔍</div>
        <h3>暂无匹配数据</h3>
        <p>尝试切换筛选条件或刷新数据</p>
      </div>`;
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

    return `
      <div class="hotspot-card fade-in ${selectedHotspotId === item.id ? 'selected' : ''}"
           data-id="${item.id}" data-source="${escapeHTML(item.source)}"
           style="animation-delay: ${idx * 0.04}s">
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
        </div>
        <div class="card-actions">
          <button class="card-action-btn primary" onclick="event.stopPropagation(); generateForHotspot(${item.id})">
            ✍️ 生成文章
          </button>
          <button class="card-action-btn" onclick="event.stopPropagation(); analyzeForHotspot(${item.id})">
            📊 分析
          </button>
          <button class="card-action-btn" onclick="event.stopPropagation(); copyTitle('${escapeAttr(item.title)}')">
            📋 标题
          </button>
        </div>
      </div>`;
  }).join('');
}

function getMetaHTML(item) {
  const parts = [];
  if (item.points) parts.push(`<span>⬆ ${item.points}</span>`);
  if (item.upvotes) parts.push(`<span>⬆ ${item.upvotes}</span>`);
  if (item.stars) parts.push(`<span>⭐ ${item.stars}</span>`);
  if (item.comments) parts.push(`<span>💬 ${item.comments}</span>`);
  if (item.author) parts.push(`<span>👤 u/${escapeHTML(item.author)}</span>`);
  if (item.heat) parts.push(`<span>🔥 ${escapeHTML(String(item.heat))}</span>`);
  if (item.language) parts.push(`<span>📝 ${escapeHTML(item.language)}</span>`);
  if (item.timestamp) parts.push(`<span>🕐 ${formatRelativeTime(item.timestamp)}</span>`);
  return parts.join('');
}

// ========================
// 联合筛选：数据源 + 内容风格
// ========================

function filterBySource(source) {
  currentSourceFilter = source;

  // 更新按钮状态
  document.querySelectorAll('#sourceFilterTabs .filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === source);
  });

  applyFilters();
}

function filterByCategory(category) {
  currentCategoryFilter = category;

  // 更新按钮状态
  document.querySelectorAll('#categoryFilterTabs .filter-tab').forEach(tab => {
    tab.classList.toggle('active', tab.dataset.filter === category);
  });

  applyFilters();
}

function applyFilters() {
  let filtered = allHotspots;

  if (currentSourceFilter !== 'all') {
    filtered = filtered.filter(h => h.source === currentSourceFilter);
  }

  if (currentCategoryFilter !== 'all') {
    filtered = filtered.filter(h => getContentType(h.source) === currentCategoryFilter);
  }

  renderHotspots(filtered);
}

function getStyleName(style) {
  const map = {
    'professional_depth': '专业深度',
    'humorous': '幽默风趣',
    'suspenseful': '悬念吸引',
    'emotional': '情感共鸣',
  };
  return map[style] || '';
}

function getStyleIcon(style) {
  const map = {
    'professional_depth': '🔬',
    'humorous': '😄',
    'suspenseful': '🎭',
    'emotional': '💝',
  };
  return map[style] || '';
}

// ========================
// 公众号文章生成
// ========================

async function generateForHotspot(id) {
  const item = allHotspots.find(h => h.id === id);
  if (!item) return;

  if (!llmConfigured) {
    showLLMConfigToast();
    return;
  }

  selectedHotspotId = id;
  applyFilters();

  const panel = document.getElementById('tweetPanel');
  panel.style.display = 'block';
  document.getElementById('analysisPanel').style.display = 'none';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const output = document.getElementById('tweetOutput');
  output.innerHTML = `
    <div style="text-align:center;padding:30px;">
      <div class="spinner"></div>
      <p style="color:var(--text-muted);margin-top:12px;font-size:13px;">大模型正在生成公众号文章...</p>
    </div>`;

  const style = document.getElementById('styleSelect').value;

  try {
    const resp = await fetch('/api/generate-tweet', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        hotspot_id: id,
        style: style,
        hotspots: [item]
      })
    });

    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.error || `HTTP ${resp.status}`);
    }

    // 判断是流式响应（SSE）还是普通 JSON
    const contentType = resp.headers.get('content-type') || '';
    if (contentType.includes('text/event-stream')) {
      await handleStreamResponse(resp, item, style);
    } else {
      const data = await resp.json();
      renderArticle(data);
    }
  } catch (err) {
    output.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠️</div>
        <h3>生成失败</h3>
        <p>${escapeHTML(err.message)}</p>
      </div>`;
    showToast('❌ 文章生成失败', 'error');
  }
}

async function handleStreamResponse(resp, item, style) {
  const output = document.getElementById('tweetOutput');
  const reader = resp.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let articleTitle = '';
  let articleContent = '';
  let meta = null;

  output.innerHTML = `
    <div style="margin-bottom:16px;">
      <span style="font-size:13px;color:var(--text-muted);">基于话题：</span>
      <strong style="font-size:14px;">${escapeHTML(item.title)}</strong>
    </div>
    <div class="article-card fade-in" id="streamingCard">
      <div class="article-title-stream" id="streamingTitle" style="min-height:28px;">
        <span class="streaming-cursor">▌</span>
      </div>
      <div class="article-content-stream" id="streamingContent" style="min-height:40px;">
        <span class="thinking-hint" id="thinkingHint" style="display:none;color:var(--text-muted);font-size:13px;">
          <span class="spinner" style="width:14px;height:14px;display:inline-block;margin-right:8px;"></span>
        </span>
      </div>
    </div>`;

  let isInContent = false;
  let thinkingActive = false;

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const dataStr = line.slice(6);
        if (dataStr === '[DONE]') continue;

        try {
          const event = JSON.parse(dataStr);

          if (event.type === 'thinking') {
            // 模型正在思考，显示进度提示
            thinkingActive = true;
            const hint = document.getElementById('thinkingHint');
            if (hint) {
              hint.style.display = 'block';
              hint.innerHTML = '<span class="spinner" style="width:14px;height:14px;display:inline-block;margin-right:8px;"></span>' + escapeHTML(event.message || '模型正在思考...');
            }
          } else if (event.type === 'title') {
            // 隐藏思考提示
            thinkingActive = false;
            const hint = document.getElementById('thinkingHint');
            if (hint) hint.style.display = 'none';

            articleTitle = event.content;
            document.getElementById('streamingTitle').innerHTML = `<span class="article-title-text">${escapeHTML(articleTitle)}</span>`;
            isInContent = true;
            document.getElementById('streamingContent').innerHTML = '';
          } else if (event.type === 'chunk') {
            // 隐藏思考提示
            if (thinkingActive) {
              thinkingActive = false;
              const hint = document.getElementById('thinkingHint');
              if (hint) hint.style.display = 'none';
            }
            articleContent += event.content;
            document.getElementById('streamingContent').innerHTML = formatArticleContent(articleContent) + '<span class="streaming-cursor">▌</span>';
          } else if (event.type === 'done') {
            meta = event;
            // 清除光标
            document.getElementById('streamingContent').innerHTML = formatArticleContent(articleContent);
          } else if (event.type === 'error') {
            output.innerHTML = `
              <div class="empty-state">
                <div class="empty-icon">⚠️</div>
                <h3>生成失败</h3>
                <p>${escapeHTML(event.message)}</p>
              </div>`;
            return;
          }
        } catch (e) {
          console.warn('Failed to parse SSE event:', dataStr);
        }
      }
    }
  } catch (err) {
    console.error('Stream read error:', err);
    // 网络错误时，如果已经有部分内容，仍然显示
    if (articleContent) {
      renderStreamArticle(articleTitle, articleContent, item, style, meta);
      return;
    }
  }

  if (articleContent) {
    if (meta && meta.article) {
      articleTitle = meta.article.title || articleTitle;
      articleContent = meta.article.content || articleContent;
    }
    renderStreamArticle(articleTitle, articleContent, item, style, meta);
  } else {
    output.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠️</div>
        <h3>生成失败</h3>
        <p>流式响应未返回有效文章内容 · 请检查大模型配置或稍后重试</p>
      </div>`;
  }
}

function renderStreamArticle(title, content, item, style, meta) {
  const output = document.getElementById('tweetOutput');

  let html = `
    <div style="margin-bottom:16px;">
      <span style="font-size:13px;color:var(--text-muted);">基于话题：</span>
      <strong style="font-size:14px;">${escapeHTML(item.title)}</strong>
      <span style="font-size:12px;color:var(--accent-purple);margin-left:8px;">· ${escapeHTML(meta?.style_name || style)}风格</span>
    </div>
    <div class="article-card fade-in">
      <div class="article-title-display">${escapeHTML(title)}</div>
      <div class="article-content-display">${formatArticleContent(content)}</div>
      <div class="article-actions">
        <button class="tweet-action-btn copy-btn" onclick="event.stopPropagation(); copyArticle('${escapeAttr(title)}', '${escapeAttr(content)}', this)">
          📋 复制全文
        </button>
        <button class="tweet-action-btn download-btn" onclick="event.stopPropagation(); downloadMarkdown('${escapeAttr(title)}', '${escapeAttr(content)}', this)">
          📥 下载Markdown
        </button>
        <span style="font-size:12px;color:var(--text-muted);margin-left:8px;">约 ${estimateWordCount(content)} 字</span>
      </div>
    </div>
    <div class="generation-tips">
      <h4>💡 公众号运营小贴士</h4>
      <ul class="tips-list">
        <li>配图可增加 150% 的阅读量</li>
        <li>发布时间建议在早 8 点或晚 8 点</li>
        <li>朋友圈转发时配一段引语效果更佳</li>
        <li>在文末添加引导关注和互动话术</li>
      </ul>
    </div>`;

  output.innerHTML = html;
}

function generateAll() {
  if (allHotspots.length === 0) {
    showToast('⚠️ 请先刷新热点数据', 'error');
    return;
  }
  generateForHotspot(allHotspots[0].id);
}

function generateMultiHotspot() {
  if (allHotspots.length === 0) {
    showToast('⚠️ 请先刷新热点数据', 'error');
    return;
  }
  if (!llmConfigured) {
    showLLMConfigToast();
    return;
  }

  const panel = document.getElementById('tweetPanel');
  panel.style.display = 'block';
  document.getElementById('analysisPanel').style.display = 'none';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const output = document.getElementById('tweetOutput');
  output.innerHTML = `
    <div style="text-align:center;padding:30px;">
      <div class="spinner"></div>
      <p style="color:var(--text-muted);margin-top:12px;font-size:13px;">大模型正在综合 ${allHotspots.length} 条热点生成综述文章...</p>
    </div>`;

  const style = document.getElementById('styleSelect').value;

  fetch('/api/export/full-article', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ hotspots: allHotspots, style: style })
  })
  .then(async resp => {
    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.error || `HTTP ${resp.status}`);
    }
    return resp.json();
  })
  .then(data => {
    const article = data.article || {};
    renderMultiArticle(data, article);
  })
  .catch(err => {
    output.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠️</div>
        <h3>生成失败</h3>
        <p>${escapeHTML(err.message)}</p>
      </div>`;
    showToast('❌ 综述文章生成失败', 'error');
  });
}

function renderMultiArticle(data, article) {
  const output = document.getElementById('tweetOutput');
  const count = data.hotspot_count || 0;
  let html = `
    <div style="margin-bottom:16px;">
      <span style="font-size:13px;color:var(--text-muted);">综合 ${count} 条热点</span>
      <span style="font-size:12px;color:var(--accent-purple);margin-left:8px;">· ${escapeHTML(data.style_name || '')}风格</span>
    </div>
    <div class="article-card fade-in">
      <div class="article-title-display">${escapeHTML(article.title || '今日热点综述')}</div>
      <div class="article-content-display">${formatArticleContent(article.content || '')}</div>
      <div class="article-actions">
        <button class="tweet-action-btn copy-btn" onclick="event.stopPropagation(); copyArticle('${escapeAttr(article.title || '')}', '${escapeAttr(article.content || '')}', this)">
          📋 复制全文
        </button>
        <button class="tweet-action-btn download-btn" onclick="event.stopPropagation(); downloadMarkdown('${escapeAttr(article.title || '')}', '${escapeAttr(article.content || '')}', this)">
          📥 下载Markdown
        </button>
        <span style="font-size:12px;color:var(--text-muted);margin-left:8px;">约 ${estimateWordCount(article.content || '')} 字</span>
      </div>
    </div>
    <div class="generation-tips">
      <h4>💡 公众号运营小贴士</h4>
      <ul class="tips-list">
        <li>配图可增加 150% 的阅读量</li>
        <li>发布时间建议在早 8 点或晚 8 点</li>
        <li>朋友圈转发时配一段引语效果更佳</li>
        <li>在文末添加引导关注和互动话术</li>
      </ul>
    </div>`;
  output.innerHTML = html;
  showToast('✅ 综述文章生成完毕', 'success');
}

function generateSelectedTweet() {
  if (selectedHotspotId !== null) {
    generateForHotspot(selectedHotspotId);
  }
}

// ========================
// 热点监控分析（LLM 生成结构化监控数据）
// ========================
async function analyzeForHotspot(id) {
  const item = allHotspots.find(h => h.id === id);
  if (!item) return;

  if (!llmConfigured) {
    showLLMConfigToast();
    return;
  }

  selectedHotspotId = id;
  applyFilters();

  const panel = document.getElementById('analysisPanel');
  panel.style.display = 'block';
  document.getElementById('tweetPanel').style.display = 'none';
  panel.scrollIntoView({ behavior: 'smooth', block: 'start' });

  const output = document.getElementById('analysisOutput');
  output.innerHTML = `
    <div style="text-align:center;padding:30px;">
      <div class="spinner"></div>
      <p style="color:var(--text-muted);margin-top:12px;font-size:13px;">大模型正在分析热点...</p>
    </div>`;

  try {
    const resp = await fetch('/api/analyze-hotspot', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ hotspot: item })
    });

    if (!resp.ok) {
      const errData = await resp.json().catch(() => ({}));
      throw new Error(errData.error || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    renderAnalysis(data.analysis, item);
  } catch (err) {
    output.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠️</div>
        <h3>分析失败</h3>
        <p>${escapeHTML(err.message)}</p>
      </div>`;
    showToast('❌ 分析失败', 'error');
  }
}

function renderAnalysis(a, item) {
  const output = document.getElementById('analysisOutput');
  if (!a || a.error) {
    output.innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚠️</div>
        <h3>分析失败</h3>
        <p>${escapeHTML(a?.error || '未知错误')}</p>
      </div>`;
    return;
  }

  const points = (a.key_points || []).map(p => `<li>${escapeHTML(p)}</li>`).join('');
  const topics = (a.topics || []).map(t => `<span class="tag">#${escapeHTML(t)}</span>`).join('');

  output.innerHTML = `
    <div class="analysis-grid fade-in">
      <div class="analysis-block">
        <h4>📝 摘要</h4>
        <p>${escapeHTML(a.summary || '')}</p>
      </div>
      <div class="analysis-metrics">
        <div class="metric"><span class="metric-label">情感倾向</span><span class="metric-value">${escapeHTML(a.sentiment || '未知')}</span></div>
        <div class="metric"><span class="metric-label">趋势</span><span class="metric-value">${escapeHTML(a.trend || '未知')}</span></div>
        <div class="metric"><span class="metric-label">内容价值</span><span class="metric-value">${escapeHTML(String(a.relevance ?? 0))}/100</span></div>
      </div>
      <div class="analysis-block">
        <h4>🔑 关键点</h4>
        <ul class="analysis-points">${points || '<li>—</li>'}</ul>
      </div>
      <div class="analysis-block">
        <h4>🎯 适合受众</h4>
        <p>${escapeHTML(a.audience || '—')}</p>
        <div class="tag-list">${topics || ''}</div>
      </div>
    </div>
    <div style="margin-top:16px;">
      <button class="tweet-action-btn copy-btn" onclick="event.stopPropagation(); generateForHotspot(${item.id})">
        ✍️ 基于此分析生成文章
      </button>
    </div>`;
}

// ========================
// 渲染公众号文章
// ========================

function renderArticle(data) {
  const output = document.getElementById('tweetOutput');
  const article = data.article || {};

  let html = `
    <div style="margin-bottom:16px;">
      <span style="font-size:13px;color:var(--text-muted);">基于话题：</span>
      <strong style="font-size:14px;">${escapeHTML(data.hotspot)}</strong>
      <span style="font-size:12px;color:var(--accent-purple);margin-left:8px;">· ${escapeHTML(data.style_name)}风格</span>
    </div>
    <div class="article-card fade-in">
      <div class="article-title-display">${escapeHTML(article.title || '')}</div>
      <div class="article-content-display">${formatArticleContent(article.content || '')}</div>
      <div class="article-actions">
        <button class="tweet-action-btn copy-btn" onclick="event.stopPropagation(); copyArticle('${escapeAttr(article.title || '')}', '${escapeAttr(article.content || '')}', this)">
          📋 复制全文
        </button>
        <button class="tweet-action-btn download-btn" onclick="event.stopPropagation(); downloadMarkdown('${escapeAttr(article.title || '')}', '${escapeAttr(article.content || '')}', this)">
          📥 下载Markdown
        </button>
        <span style="font-size:12px;color:var(--text-muted);margin-left:8px;">约 ${estimateWordCount(article.content || '')} 字</span>
      </div>
    </div>`;

  if (data.tips && data.tips.length > 0) {
    html += `
      <div class="generation-tips">
        <h4>💡 公众号运营小贴士</h4>
        <ul class="tips-list">
          ${data.tips.map(t => `<li>${escapeHTML(t)}</li>`).join('')}
        </ul>
      </div>`;
  }

  output.innerHTML = html;
}

// ========================
// 文章辅助函数
// ========================

function formatArticleContent(content) {
  let html = escapeHTML(content);
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\n\n/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');
  html = '<p>' + html + '</p>';
  return html;
}

function copyArticle(title, content, btn) {
  const fullText = title + '\n\n' + content;
  navigator.clipboard.writeText(fullText).then(() => {
    const originalText = btn.innerHTML;
    btn.innerHTML = '✅ 已复制!';
    btn.style.background = 'var(--accent-green)';
    btn.style.borderColor = 'var(--accent-green)';
    setTimeout(() => {
      btn.innerHTML = originalText;
      btn.style.background = '';
      btn.style.borderColor = '';
    }, 2000);
    showToast('📋 文章已复制到剪贴板', 'success');
  }).catch(() => {
    fallbackCopy(fullText);
  });
}

function showLLMConfigToast() {
  showToast('⚠️ 请先在「设置」中配置大模型 API', 'error');
  const output = document.getElementById('tweetOutput');
  output.innerHTML = `
    <div class="empty-state">
      <div class="empty-icon">🔧</div>
      <h3>尚未配置大模型</h3>
      <p>文章生成与热点分析需要调用你配置的大模型。请前往设置页配置 API Key。</p>
      <a class="btn btn-primary" href="/settings">前往设置</a>
    </div>`;
  document.getElementById('tweetPanel').style.display = 'block';
  document.getElementById('tweetPanel').scrollIntoView({ behavior: 'smooth', block: 'start' });
}

// ========================
// 下载 Markdown 文件
// ========================

function downloadMarkdown(title, content, btn) {
  // 构建 Markdown 内容
  const now = new Date();
  const dateStr = now.getFullYear() + '-' +
    String(now.getMonth()+1).padStart(2,'0') + '-' +
    String(now.getDate()).padStart(2,'0') + ' ' +
    String(now.getHours()).padStart(2,'0') + ':' +
    String(now.getMinutes()).padStart(2,'0');

  const md = `# ${title}\n\n> 生成时间：${dateStr} · TrendArticle\n\n---\n\n${content}\n\n---\n\n*本文由 TrendArticle 自动生成*\n`;

  // 创建下载链接
  const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  // 生成安全的文件名
  const safeTitle = title.replace(/[^\w\u4e00-\u9fff]/g, '_').substring(0, 50);
  a.download = `${safeTitle}.md`;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);

  // 按钮反馈
  const originalText = btn.innerHTML;
  btn.innerHTML = '✅ 已下载!';
  btn.style.background = 'var(--accent-green)';
  btn.style.borderColor = 'var(--accent-green)';
  setTimeout(() => {
    btn.innerHTML = originalText;
    btn.style.background = '';
    btn.style.borderColor = '';
  }, 2000);
  showToast('📥 Markdown 文件已下载', 'success');
}
