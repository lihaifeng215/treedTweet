/* ============================================
   渐进式创作工作流 · 交互逻辑
   ============================================ */

// 全局状态
let currentWorkflow = null;
let isProcessing = false;
let articleContentFull = '';

const STEP_NAMES = {
  parse: '素材解析',
  research: '搜索调研',
  topics: '选题讨论',
  outline: '大纲确认',
  generate: '正文生成',
  verify: '检验输出',
};

const STEP_ORDER = ['parse', 'research', 'topics', 'outline', 'generate', 'verify'];

// --- 初始化 ---
document.addEventListener('DOMContentLoaded', () => {
  // 从 sessionStorage 读取热点（来自素材库跳转）
  const hotspotRaw = sessionStorage.getItem('pendingHotspot');
  if (hotspotRaw) {
    sessionStorage.removeItem('pendingHotspot'); // 用完即清理
    try {
      const hotspot = JSON.parse(hotspotRaw);
      const topicText = `${hotspot.title || ''}\n${hotspot.desc || hotspot.body || ''}\n来源：${hotspot.source || ''}`;
      document.getElementById('topicInput').value = topicText;
      window._pendingHotspot = hotspot;
      // 自动启动工作流
      showToast('已从素材库载入热点，正在启动创作工作流...', 'success');
      setTimeout(() => createWorkflow(), 500);
    } catch (e) {
      console.error('hotspot data parse error', e);
      showToast('热点数据解析失败，请手动操作', 'error');
    }
  }

  // radio 卡片点击
  document.querySelectorAll('.radio-card').forEach(card => {
    card.addEventListener('click', () => {
      document.querySelectorAll('.radio-card').forEach(c => c.classList.remove('active'));
      card.classList.add('active');
      card.querySelector('input').checked = true;
    });
  });

  // 开关
  ['liveResearchToggle', 'factBasedToggle'].forEach(id => {
    const el = document.getElementById(id);
    if (el) {
      el.addEventListener('click', () => el.classList.toggle('active'));
    }
  });

  // 步骤节点点击事件：允许回到前置步骤重新选择
  document.querySelectorAll('.step-node').forEach(node => {
    node.addEventListener('click', () => {
      const step = node.dataset.step;
      if (step && step !== currentWorkflow?.current_step) {
        goToStep(step);
      }
    });
  });

  // 键盘快捷键
  document.addEventListener('keydown', (e) => {
    // Ctrl/Cmd+Enter: 开始创作 / 确认大纲
    if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
      e.preventDefault();
      if (!currentWorkflow) {
        createWorkflow();
      }
    }
    // Escape: 取消当前操作
    if (e.key === 'Escape') {
      if (isProcessing) {
        setBusy(false);
        showToast('已取消当前操作', 'success');
      }
    }
    // Ctrl/Cmd+S: 导出文章
    if ((e.ctrlKey || e.metaKey) && e.key === 's' && articleContentFull) {
      e.preventDefault();
      exportArticle();
    }
  });

  // 初始渲染明细
  renderDetails();
});

// --- 工具函数 ---
function getCreateConfig() {
  const intensity = document.querySelector('input[name="intensity"]:checked')?.value || 'standard';
  const template = document.getElementById('templateSelect')?.value || 'default';
  return {
    style: document.getElementById('styleSelect').value,
    content_type: document.getElementById('contentType').value,
    template: template,
    research_intensity: intensity,
    live_research: document.getElementById('liveResearchToggle').classList.contains('active'),
    fact_based: document.getElementById('factBasedToggle').classList.contains('active'),
    citation_pref: document.getElementById('citationPref').value,
    article_max_length: window._pendingHotspot ? null : null,
  };
}

// 文章模板定义
const ARTICLE_TEMPLATES = {
  'default': {
    name: '通用文章',
    icon: '📝',
    sections: ['导语', '背景分析', '核心观点', '深入解读', '总结展望'],
  },
  'hotspot_report': {
    name: '热点快报',
    icon: '🔥',
    sections: ['事件概述', '多方观点', '影响分析', '趋势判断', '读者互动'],
  },
  'tech_analysis': {
    name: '技术解读',
    icon: '🔬',
    sections: ['技术背景', '核心原理', '关键突破', '应用场景', '未来方向'],
  },
  'product_review': {
    name: '产品评测',
    icon: '📱',
    sections: ['产品概览', '核心功能', '使用体验', '竞品对比', '购买建议'],
  },
  'opinion_piece': {
    name: '观点评论',
    icon: '💭',
    sections: ['引题设问', '核心观点', '论据支撑', '反面讨论', '结语呼吁'],
  },
  'tutorial_guide': {
    name: '教程指南',
    icon: '📖',
    sections: ['目标说明', '前置准备', '步骤详解', '注意事项', '延伸学习'],
  },
};

function setBusy(busy) {
  isProcessing = busy;
  document.getElementById('startBtn').disabled = busy;
}

// --- 创建工作流 ---
async function createWorkflow() {
  const topicInput = document.getElementById('topicInput');
  const topicText = topicInput.value.trim();
  if (!topicText) {
    showToast('请输入创作主题或从素材库选择热点', 'error');
    return;
  }
  if (isProcessing) return;

  setBusy(true);
  articleContentFull = '';

  let source = 'custom';
  let sourceData = {};
  if (window._pendingHotspot) {
    source = 'hotspot';
    sourceData = window._pendingHotspot;
  } else {
    sourceData = { topic: topicText, context: '' };
  }

  const config = getCreateConfig();

  try {
    const resp = await fetch('/api/workflow/create', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ source, source_data: sourceData, config }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '创建工作流失败', 'error');
      setBusy(false);
      return;
    }
    currentWorkflow = data.workflow;
    renderProgress();
    renderDetails();
    showToast('工作流已创建，开始素材解析...', 'success');

    // 自动执行步骤 1 → 2 → 3
    await runParse();
  } catch (e) {
    showToast('网络错误：' + e.message, 'error');
    setBusy(false);
  }
}

// --- 步骤1：素材解析 ---
async function runParse() {
  showStepLoading('parse', '正在解析素材...');
  addProgressLog('🤖 调用 LLM 分析素材内容...');
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/parse`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '素材解析失败', 'error');
      setBusy(false);
      return;
    }
    addProgressLog('✅ 素材解析完成');
    currentWorkflow = data.workflow;
    renderParseResult(data.result);
    renderProgress();
    renderDetails();
    // 自动进入步骤2
    await runResearch();
  } catch (e) {
    showToast('素材解析出错：' + e.message, 'error');
    setBusy(false);
  }
}

function renderParseResult(result) {
  const area = document.getElementById('stepContentArea');
  const concepts = (result.concepts || []).map(c => `<span class="tag-item">${c}</span>`).join('');
  const angles = (result.angles || []).map(a => `<span class="tag-item purple">${a}</span>`).join('');
  const keywords = (result.keywords || []).map(k => `<span class="tag-item">${k}</span>`).join('');
  area.innerHTML = `
    <div class="step-result-card">
      <h4>📋 素材解析结果</h4>
      ${result.summary ? `<div class="result-text" style="margin-bottom:14px;">${result.summary}</div>` : ''}
      ${concepts ? `<div style="margin-bottom:12px;"><div class="field-label" style="margin-bottom:6px;">核心概念</div><div class="tag-list">${concepts}</div></div>` : ''}
      ${angles ? `<div style="margin-bottom:12px;"><div class="field-label" style="margin-bottom:6px;">切入角度</div><div class="tag-list">${angles}</div></div>` : ''}
      ${result.audience ? `<div style="margin-bottom:12px;"><div class="field-label" style="margin-bottom:6px;">目标受众</div><div class="result-text">${result.audience}</div></div>` : ''}
      ${keywords ? `<div><div class="field-label" style="margin-bottom:6px;">搜索关键词</div><div class="tag-list">${keywords}</div></div>` : ''}
    </div>
  `;
  scrollToContent();
}

// --- 步骤2：搜索调研 ---
async function runResearch() {
  showStepLoading('research', '正在联网搜索调研...');
  showProgressBar(true);
  setProgressPercent(5);
  addProgressLog('🔍 根据素材解析结果生成搜索计划...');
  
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/research`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    setProgressPercent(50);
    addProgressLog('🌐 正在实时联网搜索...');
    
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '搜索调研失败', 'error');
      setBusy(false);
      return;
    }
    setProgressPercent(85);
    addProgressLog('📊 分析搜索结果并生成研究摘要...');
    
    currentWorkflow = data.workflow;
    setProgressPercent(100);
    addProgressLog(`✅ 搜索调研完成 · 获取 ${(data.result?.results || []).length} 条搜索结果`);
    
    renderResearchResult(data.result);
    renderProgress();
    renderDetails();
    // 自动进入步骤3
    await runTopics();
  } catch (e) {
    showToast('搜索调研出错：' + e.message, 'error');
    setBusy(false);
  }
}

function renderResearchResult(result) {
  const area = document.getElementById('stepContentArea');
  const items = (result.results || []).map(r => `
    <div class="research-item">
      <a class="research-item-title" href="${r.url}" target="_blank">${r.title || '无标题'}</a>
      <div class="research-item-snippet">${r.snippet || ''}</div>
      ${r.query ? `<div class="research-item-query">搜索词：${r.query}</div>` : ''}
    </div>
  `).join('');
  area.innerHTML = `
    <div class="step-result-card">
      <h4>🔍 搜索调研结果 <span style="font-size:12px;color:var(--text-muted);font-weight:400;">共 ${result.total_found || 0} 条，展示 ${result.results?.length || 0} 条</span></h4>
      ${result.plan?.rationale ? `<div class="result-text" style="margin-bottom:14px;">${result.plan.rationale}</div>` : ''}
      <div class="research-list">${items || '<div class="result-text">暂无搜索结果</div>'}</div>
    </div>
  `;
  scrollToContent();
}

// --- 步骤3：选题讨论 ---
async function runTopics() {
  showStepLoading('topics', '正在生成候选选题...');
  addProgressLog('🤖 正在基于调研结果生成候选选题...');
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/topics`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '生成选题失败', 'error');
      setBusy(false);
      return;
    }
    addProgressLog(`✅ 已生成 ${(data.result?.topics || []).length} 个候选选题，请选择一个`);
    currentWorkflow = data.workflow;
    renderTopics(data.result);
    renderProgress();
    renderDetails();
    setBusy(false); // 等待用户选择
  } catch (e) {
    showToast('生成选题出错：' + e.message, 'error');
    setBusy(false);
  }
}

function renderTopics(result) {
  const area = document.getElementById('stepContentArea');
  const topics = result.topics || [];
  const cards = topics.map(t => `
    <div class="topic-card" onclick="selectTopic('${t.id}')" data-topic-id="${t.id}">
      <div class="topic-card-title">${t.title}</div>
      ${t.angle ? `<div class="topic-card-angle">${t.angle}</div>` : ''}
      <div class="topic-card-summary">${t.summary || ''}</div>
    </div>
  `).join('');
  area.innerHTML = `
    <div class="step-result-card">
      <h4>💡 候选选题 <span style="font-size:12px;color:var(--text-muted);font-weight:400;">请选择一个选题继续</span></h4>
      <div class="topics-grid">${cards}</div>
    </div>
  `;
  const bar = document.getElementById('actionBar');
  bar.style.display = 'none';
  scrollToContent();
}

async function selectTopic(topicId) {
  if (isProcessing) return;
  const topics = (currentWorkflow.steps?.find(s => s.step_name === 'topics')?.output?.topics) || [];
  const topic = topics.find(t => t.id === topicId);
  if (!topic) return;

  // 高亮选中
  document.querySelectorAll('.topic-card').forEach(c => c.classList.remove('selected'));
  document.querySelector(`[data-topic-id="${topicId}"]`)?.classList.add('selected');

  setBusy(true);
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/topic/select`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ topic }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '选择失败', 'error');
      setBusy(false);
      return;
    }
    currentWorkflow = data.workflow;
    renderProgress();
    renderDetails();
    showToast('已选择选题，正在生成大纲...', 'success');
    await runOutline();
  } catch (e) {
    showToast('出错：' + e.message, 'error');
    setBusy(false);
  }
}

// --- 步骤4：大纲确认 ---
async function runOutline() {
  showStepLoading('outline', '正在生成文章大纲...');
  addProgressLog('🤖 正在根据选题生成文章大纲...');
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/outline`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '生成大纲失败', 'error');
      setBusy(false);
      return;
    }
    addProgressLog(`✅ 大纲已生成，共 ${(data.result?.sections || []).length} 个章节`);
    currentWorkflow = data.workflow;
    renderOutline(data.result);
    renderProgress();
    renderDetails();
    setBusy(false); // 等待用户确认
  } catch (e) {
    showToast('生成大纲出错：' + e.message, 'error');
    setBusy(false);
  }
}

function renderOutline(result) {
  const area = document.getElementById('stepContentArea');
  const sections = result.sections || [];
  const sectionsHtml = sections.map((s, i) => `
    <div class="outline-section">
      <div class="outline-section-header">
        <div class="outline-section-num">${i + 1}</div>
        <input class="outline-section-input" value="${s.heading || ''}" data-idx="${i}" data-field="heading">
      </div>
      <ul class="outline-points">
        ${(s.points || []).map(p => `<li>${p}</li>`).join('')}
      </ul>
    </div>
  `).join('');

  area.innerHTML = `
    <div class="outline-editor">
      <div class="field-label" style="margin-bottom:6px;">文章标题（可编辑）</div>
      <input class="outline-title-input" id="outlineTitle" value="${result.title || ''}">
      <div class="field-label" style="margin:16px 0 10px;">章节大纲（标题可编辑）</div>
      ${sectionsHtml}
    </div>
  `;

  const bar = document.getElementById('actionBar');
  bar.style.display = 'flex';
  bar.innerHTML = `
    <button class="action-btn" onclick="regenerateOutline()">🔄 重新生成</button>
    <button class="action-btn primary" onclick="confirmOutline()">✓ 确认大纲，开始生成正文</button>
  `;
  scrollToContent();
}

async function confirmOutline() {
  if (isProcessing) return;
  // 收集编辑后的大纲
  const title = document.getElementById('outlineTitle').value;
  const sections = [];
  document.querySelectorAll('.outline-section').forEach((sec, i) => {
    const headingInput = sec.querySelector('.outline-section-input');
    const points = Array.from(sec.querySelectorAll('.outline-points li')).map(li => li.textContent);
    sections.push({ heading: headingInput.value, points });
  });
  const outline = { title, sections };

  setBusy(true);
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/outline/confirm`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ outline }),
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '确认失败', 'error');
      setBusy(false);
      return;
    }
    currentWorkflow = data.workflow;
    renderProgress();
    renderDetails();
    showToast('大纲已确认，开始流式生成正文...', 'success');
    await runGenerate();
  } catch (e) {
    showToast('出错：' + e.message, 'error');
    setBusy(false);
  }
}

async function regenerateOutline() {
  if (isProcessing) return;
  setBusy(true);
  await runOutline();
}

// --- 步骤5：正文生成（SSE 流式）---
async function runGenerate() {
  const area = document.getElementById('stepContentArea');
  showStepLoading('generate', '正在流式生成正文...');
  addProgressLog('🤖 LLM 正在思考并生成文章内容...');
  addProgressLog('💡 正文将逐段实时显示，请稍候...');
  
  area.innerHTML = `
    <div class="article-stream-area">
      <div id="streamThinking"></div>
      <div class="article-title-display" id="streamTitle"></div>
      <div class="article-content-display cursor-blink" id="streamContent"></div>
    </div>
  `;
  document.getElementById('actionBar').style.display = 'none';
  articleContentFull = '';
  scrollToContent();

  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/generate`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });

    // 检查 HTTP 状态码，处理非 SSE 格式的错误响应
    if (!resp.ok) {
      let errMsg = `服务器返回 ${resp.status}`;
      try {
        const errData = await resp.json();
        errMsg = errData.error || errMsg;
      } catch (_) {}
      showToast('正文生成失败：' + errMsg, 'error');
      setBusy(false);
      await refreshWorkflow();
      renderProgress();
      renderDetails();
      return;
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split('\n');
      buffer = lines.pop() || '';

      for (const line of lines) {
        if (!line.startsWith('data: ')) continue;
        const dataStr = line.slice(6).trim();
        if (dataStr === '[DONE]') continue;
        try {
          const evt = JSON.parse(dataStr);
          handleStreamEvent(evt);
        } catch (e) { /* ignore */ }
      }
    }
    // 处理缓冲区剩余数据（最后一行可能不完整）
    if (buffer.trim()) {
      const line = buffer.trim();
      if (line.startsWith('data: ')) {
        const dataStr = line.slice(6).trim();
        if (dataStr !== '[DONE]') {
          try { handleStreamEvent(JSON.parse(dataStr)); } catch (e) {}
        }
      }
    }
  } catch (e) {
    showToast('正文生成出错：' + e.message, 'error');
    setBusy(false);
  }
}

function handleStreamEvent(evt) {
  const thinkingEl = document.getElementById('streamThinking');
  const titleEl = document.getElementById('streamTitle');
  const contentEl = document.getElementById('streamContent');

  if (evt.type === 'thinking') {
    if (thinkingEl) thinkingEl.innerHTML = `<div class="thinking-indicator">${evt.message}</div>`;
  } else if (evt.type === 'title') {
    if (thinkingEl) thinkingEl.innerHTML = '';
    if (titleEl) titleEl.textContent = evt.content;
  } else if (evt.type === 'chunk') {
    if (thinkingEl) thinkingEl.innerHTML = '';
    articleContentFull += evt.content;
    if (contentEl) contentEl.innerHTML = formatContent(articleContentFull);
  } else if (evt.type === 'error') {
    showToast(evt.message, 'error');
    setBusy(false);
  } else if (evt.type === 'done') {
    if (contentEl) contentEl.classList.remove('cursor-blink');
    const art = evt.article || {};
    if (titleEl && art.title) titleEl.textContent = art.title;
    articleContentFull = art.content || articleContentFull;
    if (contentEl) contentEl.innerHTML = formatContent(articleContentFull);
    // 刷新工作流状态
    refreshWorkflow().then(() => {
      renderProgress();
      renderDetails();
      showGenerateActions();
      setBusy(false);
    });
  }
}

function formatContent(text) {
  if (!text) return '';
  // 先转义HTML，再处理 Markdown
  let html = escapeHTML(text);
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/\n{2,}/g, '</p><p>');
  html = html.replace(/\n/g, '<br>');
  html = '<p>' + html + '</p>';
  return html;
}

function showGenerateActions() {
  const bar = document.getElementById('actionBar');
  bar.style.display = 'flex';
  bar.innerHTML = `
    <button class="action-btn" onclick="regenerateBody()">🔄 重新生成</button>
    <button class="action-btn primary" onclick="runVerify()">🔍 检验输出</button>
    <select class="export-format-select" id="exportFormat">
      <option value="markdown">📝 Markdown</option>
      <option value="wechat">💚 微信格式</option>
      <option value="html">🌐 HTML</option>
      <option value="text">📄 纯文本</option>
    </select>
    <button class="action-btn success" onclick="exportArticle()">📥 导出</button>
  `;
}

async function regenerateBody() {
  if (isProcessing) return;
  setBusy(true);
  articleContentFull = '';
  await runGenerate();
}

// --- 步骤6：检验输出 ---
async function runVerify() {
  if (isProcessing) return;
  setBusy(true);
  showStepLoading('verify', '正在检验文章质量...');
  addProgressLog('🔍 正在对文章进行多维质量检验...');
  addProgressLog('📊 检查维度：完整性、事实准确性、结构逻辑...');
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/verify`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '检验失败', 'error');
      setBusy(false);
      return;
    }
    addProgressLog('✅ 质量检验完成');
    currentWorkflow = data.workflow;
    renderVerifyResult(data.result);
    renderProgress();
    renderDetails();
    setBusy(false);
  } catch (e) {
    showToast('检验出错：' + e.message, 'error');
    setBusy(false);
  }
}

function renderVerifyResult(result) {
  const area = document.getElementById('stepContentArea');
  const completeness = result.completeness || 0;
  const quality = result.quality_score || 0;
  const scoreClass = (v) => v >= 80 ? 'high' : (v >= 60 ? 'mid' : 'low');
  const verdict = result.verdict || '通过';
  const verdictClass = verdict.includes('通过') || verdict.includes('pass') ? 'verdict-pass' : 'verdict-revise';

  const issues = (result.issues || []).map(i => `<li>${i}</li>`).join('');
  const suggestions = (result.suggestions || []).map(s => `<li>${s}</li>`).join('');

  area.innerHTML = `
    <div class="verify-report">
      <div class="score-row">
        <div class="score-card">
          <div class="score-value ${scoreClass(completeness)}">${completeness}</div>
          <div class="score-label">完整度</div>
        </div>
        <div class="score-card">
          <div class="score-value ${scoreClass(quality)}">${quality}</div>
          <div class="score-label">质量评分</div>
        </div>
        <div class="score-card">
          <div class="score-value" style="font-size:18px;padding-top:8px;">${result.word_count || 0}</div>
          <div class="score-label">字数</div>
        </div>
      </div>
      <div style="text-align:center;">
        <span class="verdict-badge ${verdictClass}">${verdict}</span>
      </div>
      ${issues ? `<div><div class="field-label" style="margin-bottom:8px;">发现问题</div><ul class="issue-list">${issues}</ul></div>` : ''}
      ${suggestions ? `<div><div class="field-label" style="margin-bottom:8px;">改进建议</div><ul class="suggestion-list">${suggestions}</ul></div>` : ''}
    </div>
  `;

  const bar = document.getElementById('actionBar');
  bar.style.display = 'flex';
  bar.innerHTML = `
    <button class="action-btn" onclick="runVerify()">🔄 重新检验</button>
    <select class="export-format-select" id="exportFormat">
      <option value="markdown">📝 Markdown</option>
      <option value="wechat">💚 微信格式</option>
      <option value="html">🌐 HTML</option>
      <option value="text">📄 纯文本</option>
    </select>
    <button class="action-btn success" onclick="exportArticle()">📥 导出</button>
  `;
  scrollToContent();
}

async function exportArticle() {
  if (!currentWorkflow) return;
  try {
    const formatEl = document.getElementById('exportFormat');
    const format = formatEl ? formatEl.value : 'markdown';

    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/export`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: '{}',
    });
    if (!resp.ok) {
      showToast('导出失败', 'error');
      return;
    }

    // 解码 RFC 5987 filename*=UTF-8'' 编码
    const disposition = resp.headers.get('Content-Disposition') || '';
    let filename = '文章';
    const m1 = disposition.match(/filename\*=UTF-8''(.+)/);
    if (m1) {
      try { filename = decodeURIComponent(m1[1]); } catch(e) {}
    } else {
      const m2 = disposition.match(/filename="(.+?)"/);
      if (m2) filename = m2[1];
    }
    // 去掉 .md 后缀以便加新后缀
    filename = filename.replace(/\.md$/i, '');

    if (format === 'markdown') {
      // 原生 Markdown，直接下载 blob
      const blob = await resp.blob();
      downloadBlob(blob, filename + '.md');
    } else if (format === 'wechat') {
      // 微信格式：HTML with inline styles for WeChat editor
      const text = await resp.text();
      const content = markdownToWechatHTML(text);
      const blob = new Blob([content], { type: 'text/html;charset=utf-8' });
      downloadBlob(blob, filename + '_微信.html');
    } else {
      // 阅读 Markdown 内容并转换
      const text = await resp.text();
      let content, ext, mime;
      if (format === 'html') {
        content = markdownToHTML(text);
        ext = '.html';
        mime = 'text/html';
      } else {
        content = stripMarkdown(text);
        ext = '.txt';
        mime = 'text/plain';
      }
      const blob = new Blob([content], { type: mime + ';charset=utf-8' });
      downloadBlob(blob, filename + ext);
    }

    showToast('文章已导出，工作流完成！', 'success');

    // 自动保存到历史
    try {
      await fetch('/api/articles', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: currentWorkflow.article_title || '未命名',
          content: currentWorkflow.article_content || '',
          style: currentWorkflow.config?.style || '',
          source: currentWorkflow.source || '',
          hotspot_title: currentWorkflow.source_data?.title || '',
          word_count: currentWorkflow.article_content ? estimateWordCount(currentWorkflow.article_content) : 0,
        }),
      });
    } catch(e) {}
    // 刷新状态
    await refreshWorkflow();
    renderProgress();
    renderDetails();
  } catch (e) {
    showToast('导出出错：' + e.message, 'error');
  }
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function markdownToHTML(md) {
  let html = md;
  // 标题
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // 粗体
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  // 链接
  html = html.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2">$1</a>');
  // 段落
  html = html.replace(/\n{2,}/g, '</p><p>');
  html = '<p>' + html + '</p>';
  // 换行
  html = html.replace(/\n/g, '<br>');
  return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>TrendArticle 导出</title>
<style>
body{font-family:system-ui,-apple-system,sans-serif;max-width:800px;margin:40px auto;padding:20px;line-height:1.9;color:#333;background:#fff}
h1{font-size:1.8em;border-bottom:2px solid #eee;padding-bottom:12px}
h2{font-size:1.4em;margin-top:1.5em}
h3{font-size:1.15em;margin-top:1.2em}
a{color:#2563eb}
strong{color:#111}
p{margin:0.8em 0}
@media(prefers-color-scheme:dark){body{background:#1a1a2e;color:#e0e0e0}h1{border-color:#333}strong{color:#fff}a{color:#60a5fa}}
</style></head><body>${html}</body></html>`;
}

function stripMarkdown(md) {
  let text = md;
  text = text.replace(/^#{1,3} /gm, '');
  text = text.replace(/\*\*(.+?)\*\*/g, '$1');
  text = text.replace(/\[(.+?)\]\(.+?\)/g, '$1');
  text = text.replace(/\n{3,}/g, '\n\n');
  return text;
}

function markdownToWechatHTML(md) {
  /** 将 Markdown 转换为微信公众平台兼容的 HTML 格式 */
  let html = md;
  
  // 移除 YAML front matter / 元数据行
  html = html.replace(/^>.*\n/gm, '<blockquote style="border-left:3px solid #3b82f6;padding:8px 12px;margin:10px 0;background:#f8f9fa;color:#666;font-size:14px;">$&</blockquote>');
  
  // 标题
  html = html.replace(/^### (.+)$/gm, '<h3 style="font-size:18px;font-weight:700;margin:20px 0 10px;color:#1a1a2e;border-left:3px solid #3b82f6;padding-left:10px;">$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2 style="font-size:20px;font-weight:700;margin:24px 0 12px;color:#1a1a2e;text-align:center;">$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1 style="font-size:22px;font-weight:700;margin:24px 0 16px;color:#1a1a2e;text-align:center;">$1</h1>');
  
  // 粗体
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong style="color:#1a1a2e;">$1</strong>');
  
  // 链接（微信中需要显示为可点击的文本）
  html = html.replace(/\[(.+?)\]\((.+?)\)/g, '<a href="$2" style="color:#3b82f6;text-decoration:none;border-bottom:1px solid #3b82f6;">$1</a>');
  
  // 分割线
  html = html.replace(/^---$/gm, '<hr style="border:none;border-top:1px solid #eee;margin:24px 0;">');
  
  // 段落
  html = html.replace(/\n{2,}/g, '</p><p style="font-size:15px;line-height:1.8;color:#333;margin:10px 0;text-align:justify;">');
  html = '<p style="font-size:15px;line-height:1.8;color:#333;margin:10px 0;text-align:justify;">' + html + '</p>';
  
  // 单换行
  html = html.replace(/\n/g, '<br>');
  
  // 清理空段落
  html = html.replace(/<p[^>]*><\/p>/g, '');
  
  // 包装为完整的微信兼容 HTML
  return `<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>微信文章</title>
</head>
<body style="max-width:677px;margin:0 auto;padding:16px;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;">
<div style="text-align:center;color:#888;font-size:12px;margin-bottom:16px;">👆 点击上方蓝字关注</div>
${html}
<div style="text-align:center;margin-top:32px;padding:20px;background:linear-gradient(135deg,#667eea,#764ba2);color:#fff;border-radius:8px;">
  <p style="font-size:14px;margin:0 0 8px;">📱 <strong>扫码关注，获取更多深度内容</strong></p>
  <p style="font-size:12px;margin:0;opacity:0.8;">阅读 {{阅读数}} · 点赞 {{点赞数}} · 在看 {{在看数}}</p>
</div>
</body>
</html>`;
}

// --- 回到前置步骤 ---
async function goToStep(stepName) {
  if (isProcessing) return;
  if (!currentWorkflow) return;

  const steps = currentWorkflow.steps || [];
  const targetStep = steps.find(s => s.step_name === stepName);
  if (!targetStep) return;

  const curIdx = STEP_ORDER.indexOf(currentWorkflow.current_step);
  const targetIdx = STEP_ORDER.indexOf(stepName);
  // 只允许回到前置步骤（索引更小），或同一步骤（重试）
  if (targetIdx > curIdx) return;

  setBusy(true);
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}/reset/${stepName}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
    });
    const data = await resp.json();
    if (!resp.ok) {
      showToast(data.error || '重置失败', 'error');
      setBusy(false);
      return;
    }

    currentWorkflow = data.workflow;
    showToast(`已回到：${STEP_NAMES[stepName]}`, 'success');
    renderProgress();
    renderDetails();

    // 根据目标步骤，自动重新执行
    await rerunFromStep(stepName);
  } catch (e) {
    showToast('重置出错：' + e.message, 'error');
    setBusy(false);
  }
}

async function rerunFromStep(stepName) {
  switch (stepName) {
    case 'parse':
      await runParse();
      break;
    case 'research':
      await runResearch();
      break;
    case 'topics':
      await runTopics();
      break;
    case 'outline': {
      // 大纲需要先有选题，检查是否有选中项
      const topicsStep = currentWorkflow.steps?.find(s => s.step_name === 'topics');
      const selectedTopic = topicsStep?.output?.selected_topic;
      if (!selectedTopic) {
        await goToStep('topics');
        return;
      }
      await runOutline();
      break;
    }
    case 'generate':
      articleContentFull = '';
      await runGenerate();
      break;
    case 'verify':
      await runVerify();
      break;
  }
}

// --- 通用渲染 ---
async function refreshWorkflow() {
  if (!currentWorkflow) return;
  try {
    const resp = await fetch(`/api/workflow/${currentWorkflow.id}`);
    const data = await resp.json();
    if (resp.ok) currentWorkflow = data.workflow;
  } catch (e) { /* ignore */ }
}

function showStepLoading(step, msg) {
  const area = document.getElementById('stepContentArea');
  const stepLabel = STEP_NAMES[step] || '处理中';
  area.innerHTML = `
    <div class="loading-spinner">
      <div class="spinner"></div>
      <div class="loading-main-msg">${msg}</div>
      <div class="loading-step-label">步骤：${stepLabel}</div>
      <div class="progress-log" id="progressLog"></div>
      <div class="progress-bar" style="display:none;">
        <div class="progress-bar-fill" id="progressBarFill"></div>
      </div>
      <div class="progress-percent" id="progressPercent"></div>
    </div>
  `;
  renderProgress(step);
  scrollToContent();
}

function addProgressLog(message) {
  const log = document.getElementById('progressLog');
  if (!log) return;
  const line = document.createElement('div');
  line.className = 'progress-log-line';
  line.textContent = message;
  log.appendChild(line);
  log.scrollTop = log.scrollHeight;
}

function setProgressPercent(percent) {
  const bar = document.getElementById('progressBarFill');
  const pct = document.getElementById('progressPercent');
  if (bar) bar.style.width = percent + '%';
  if (pct) pct.textContent = percent + '%';
}

function showProgressBar(show) {
  const bar = document.querySelector('.progress-bar');
  if (bar) bar.style.display = show ? 'block' : 'none';
}

function scrollToContent() {
  const area = document.getElementById('stepContentArea');
  if (area) {
    area.scrollTo({ top: 0, behavior: 'smooth' });
  }
  // 滚动进度条使当前步骤可见
  const activeNode = document.querySelector('.step-node.active');
  if (activeNode) {
    activeNode.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
  }
}

function renderProgress(activeStep) {
  const cur = activeStep || currentWorkflow?.current_step;
  const steps = currentWorkflow?.steps || [];
  const stepMap = {};
  steps.forEach(s => { stepMap[s.step_name] = s.status; });
  const curIdx = STEP_ORDER.indexOf(cur);

  document.querySelectorAll('.step-node').forEach(node => {
    const step = node.dataset.step;
    const stepIdx = STEP_ORDER.indexOf(step);
    node.classList.remove('active', 'completed', 'waiting', 'failed', 'clickable');
    node.title = STEP_NAMES[step] || step;
    const status = stepMap[step];
    if (status === 'completed') {
      node.classList.add('completed');
      // 已完成的前置步骤可点击返回
      if (stepIdx < curIdx) {
        node.classList.add('clickable');
        node.title = `点击回到：${STEP_NAMES[step]}`;
      }
    } else if (status === 'waiting_user') {
      node.classList.add('waiting');
    } else if (status === 'failed') {
      node.classList.add('failed');
      // 失败的步骤可点击重试
      if (stepIdx <= curIdx) {
        node.classList.add('clickable');
        node.title = `点击重试：${STEP_NAMES[step]}`;
      }
    } else if (status === 'running' || step === cur) {
      node.classList.add('active');
    }
  });

  // 连线
  const connectors = document.querySelectorAll('.step-connector');
  STEP_ORDER.forEach((step, i) => {
    if (i < connectors.length) {
      const status = stepMap[step];
      if (status === 'completed') {
        connectors[i].classList.add('completed');
      } else {
        connectors[i].classList.remove('completed');
      }
    }
  });
}

function renderDetails() {
  const list = document.getElementById('detailList');
  const steps = currentWorkflow?.steps || [];
  if (steps.length === 0) {
    list.innerHTML = `<div style="color:var(--text-muted);font-size:12px;padding:20px 0;text-align:center;">开始创作后显示步骤明细</div>`;
    return;
  }

  const cur = currentWorkflow?.current_step;
  list.innerHTML = steps.map(s => {
    const name = STEP_NAMES[s.step_name] || s.step_name;
    const statusText = {
      pending: '⏳ 待执行',
      running: '⏳ 进行中',
      completed: '✅ 已完成',
      failed: '❌ 失败',
      waiting_user: '⏸ 等待用户',
    }[s.status] || s.status;
    const cls = s.status === 'completed' ? 'completed' :
               s.status === 'running' ? 'active' :
               s.status === 'waiting_user' ? 'waiting' :
               s.status === 'failed' ? 'failed' : '';
    const expanded = (s.step_name === cur || s.status === 'waiting_user') ? 'expanded' : '';
    const subTasks = (s.sub_tasks || []).map(t => {
      const icon = t.status === 'completed' ? '✓' : t.status === 'running' ? '⏳' : t.status === 'failed' ? '✗' : '○';
      return `<div class="sub-task ${t.status}"><span class="sub-task-icon">${icon}</span><span>${t.name}</span></div>`;
    }).join('');

    let outputPreview = '';
    if (s.output) {
      const outStr = typeof s.output === 'string' ? s.output : JSON.stringify(s.output, null, 2);
      if (outStr && outStr !== '{}') {
        outputPreview = `<div class="detail-output">${escapeHTML(outStr).slice(0, 500)}</div>`;
      }
    }

    return `
      <div class="detail-accordion ${cls} ${expanded}" onclick="this.classList.toggle('expanded')">
        <div class="detail-accordion-header">
          <div class="da-num">${STEP_ORDER.indexOf(s.step_name) + 1}</div>
          <div class="da-title">${name}</div>
          <div class="da-status">${statusText}</div>
          <div class="da-chevron">▶</div>
        </div>
        <div class="detail-accordion-body">
          ${subTasks || '<div class="result-text" style="font-size:11px;">暂无子任务</div>'}
          ${outputPreview}
        </div>
      </div>
    `;
  }).join('');
}


