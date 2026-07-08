/**
 * TrendArticle v6.1 · 仪表盘交互逻辑
 * 核心功能：日报展示、精选热点、信源健康、快速评分
 */

let currentDigest = null;
let scoredHotspots = [];

document.addEventListener('DOMContentLoaded', () => {
  initDashboard();
});

async function initDashboard() {
  // 并行加载
  await Promise.all([
    loadDigest(),
    loadScoredHotspots(),
    loadSourceHealth(),
  ]);
}

// ========================
// 日报加载
// ========================

async function loadDigest() {
  try {
    const resp = await fetch('/api/digest/latest');
    if (resp.ok) {
      const data = await resp.json();
      currentDigest = data.digest;
      renderDigest(data.digest);
      updateStats(data.digest.stats);
    } else {
      // 尝试从 scored hotspots 加载
      document.getElementById('digestDate').textContent = '—';
    }
  } catch (e) {
    console.error('Load digest error:', e);
  }
}

function renderDigest(digest) {
  const dateEl = document.getElementById('digestDate');
  dateEl.textContent = digest.date || '—';

  const body = document.getElementById('digestBody');
  const sections = digest.sections || [];

  if (sections.length === 0) {
    body.innerHTML = `
      <div class="empty-digest">
        <div class="empty-digest-icon">📭</div>
        <div class="empty-digest-text">暂无精选热点</div>
        <div class="empty-digest-hint">暂无满足精选标准的热点，请刷新热点数据后重新生成</div>
      </div>`;
    return;
  }

  let html = '';
  sections.forEach(section => {
    html += `
      <div class="digest-category">
        <div class="digest-cat-header">
          <span>${section.icon}</span>
          <span>${section.name}</span>
          <span class="digest-cat-count">${section.count}条</span>
        </div>`;
    
    (section.items || []).forEach((item, i) => {
      const tierClass = (item.source_tier || 'T2').toLowerCase().replace('.', '-');
      const scoreClass = item.score >= 80 ? 'high' : (item.score >= 60 ? 'mid' : 'low');
      html += `
        <a class="digest-item" href="${item.url || '#'}" target="_blank">
          <div class="digest-item-rank">${i + 1}</div>
          <div class="digest-item-content">
            <div class="digest-item-title">${escapeHTML(item.title)}</div>
            <div class="digest-item-meta">
              <span>${item.source_icon || '📡'} ${escapeHTML(item.source)}</span>
              <span class="tier-badge ${tierClass}">${item.source_tier_label || item.source_tier || 'T2'}</span>
            </div>
            ${item.desc ? `<div class="digest-item-desc">${escapeHTML(item.desc)}</div>` : ''}
          </div>
          <div class="digest-score ${scoreClass}">${item.score}分</div>
        </a>`;
    });
    
    html += '</div>';
  });

  body.innerHTML = html;
}

// ========================
// 评分热点加载
// ========================

async function loadScoredHotspots() {
  try {
    const resp = await fetch('/api/hotspots/scored?limit=50');
    if (resp.ok) {
      const data = await resp.json();
      scoredHotspots = data.hotspots || [];
      // 更新统计
      if (data.stats && !currentDigest) {
        updateStats(data.stats);
      }
    }
  } catch (e) {
    console.error('Load scored hotspots error:', e);
  }
}

function updateStats(stats) {
  document.getElementById('statTotal').textContent = stats.total || stats.total_hotspots || '—';
  document.getElementById('statSelected').textContent = stats.selected || stats.selected_count || '—';
  document.getElementById('statAvgScore').textContent = stats.avg_score || '—';
  document.getElementById('statSelectionRate').textContent = 
    `精选率 ${stats.selection_rate || stats.selection_rate || 0}%`;
  
  // 活跃信源
  const tierDist = stats.tier_distribution || {};
  const activeSources = (tierDist.T1 || 0) + (tierDist['T1.5'] || 0) + (tierDist.T2 || 0);
  document.getElementById('statSources').textContent = activeSources || '—';
  
  // 更新时间
  document.getElementById('lastUpdate').textContent = 
    `🕐 更新于 ${new Date().toLocaleTimeString('zh-CN')}`;
}

// ========================
// 信源健康
// ========================

async function loadSourceHealth() {
  try {
    const resp = await fetch('/api/health');
    if (!resp.ok) return;
    const data = await resp.json();
    const sources = data.sources || {};

    const list = document.getElementById('sourceHealthList');
    const entries = Object.entries(sources);
    
    if (entries.length === 0) {
      list.innerHTML = '<div style="color:var(--text-muted);font-size:12px;">暂无数据</div>';
      return;
    }

    // 按分级排序
    const tierOrder = { 'T1': 0, 'T1.5': 1, 'T2': 2 };
    entries.sort((a, b) => {
      const tierA = a[1].tier || 'T2';
      const tierB = b[1].tier || 'T2';
      return (tierOrder[tierA] || 9) - (tierOrder[tierB] || 9);
    });

    list.innerHTML = entries.map(([key, info]) => {
      const status = info.status || 'unknown';
      const rate = (info.success_rate || 0).toFixed(0);
      const tier = info.tier || 'T2';
      const tierClass = tier.toLowerCase().replace('.', '-');
      return `
        <div class="source-health-item">
          <div class="source-health-dot ${status}"></div>
          <div class="source-health-name">${info.icon || '📡'} ${info.name || key}</div>
          <span class="tier-badge ${tierClass}">${tier}</span>
          <span class="source-health-rate">${rate}%</span>
        </div>`;
    }).join('');
  } catch (e) {
    console.error('Load health error:', e);
  }
}

// ========================
// 日报生成
// ========================

async function generateDigest() {
  const btn = document.getElementById('genDigestBtn');
  btn.disabled = true;
  btn.textContent = '⏳ 生成中...';
  
  try {
    const resp = await fetch('/api/digest/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({}),
    });
    
    if (!resp.ok) {
      const data = await resp.json();
      showToast(data.error || '生成日报失败', 'error');
      return;
    }
    
    const data = await resp.json();
    currentDigest = data.digest;
    renderDigest(data.digest);
    updateStats(data.digest.stats);
    showToast('✅ 日报已生成！', 'success');
  } catch (e) {
    showToast('生成日报失败：' + e.message, 'error');
  } finally {
    btn.disabled = false;
    btn.textContent = '🔄 生成日报';
  }
}

async function exportLatestDigest() {
  if (!currentDigest) {
    // 尝试生成
    await generateDigest();
    if (!currentDigest) {
      showToast('请先生成日报', 'error');
      return;
    }
  }
  
  try {
    const resp = await fetch(`/api/digest/${currentDigest.date}/export`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    
    if (!resp.ok) {
      showToast('导出失败', 'error');
      return;
    }
    
    const blob = await resp.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `热点日报_${currentDigest.date}.md`;
    a.click();
    URL.revokeObjectURL(url);
    showToast('📥 日报已导出', 'success');
  } catch (e) {
    showToast('导出失败：' + e.message, 'error');
  }
}
