/**
 * TrendTweet · 设置页逻辑（大模型配置）
 */

document.addEventListener('DOMContentLoaded', () => {
  loadConfig();
  // thinking 开关联动预算输入框
  document.getElementById('thinkingEnabled').addEventListener('change', (e) => {
    document.getElementById('thinkingBudgetRow').style.display = e.target.checked ? '' : 'none';
  });
});

// ========================
// 加载当前配置
// ========================
async function loadConfig() {
  try {
    const resp = await fetch('/api/llm-config');
    const cfg = await resp.json();
    if (cfg.base_url) document.getElementById('baseUrl').value = cfg.base_url;
    if (cfg.model) document.getElementById('model').value = cfg.model;
    if (cfg.temperature !== undefined) document.getElementById('temperature').value = cfg.temperature;
    if (cfg.proxy) document.getElementById('proxyUrl').value = cfg.proxy;
    if (cfg.api_key_set) {
      document.getElementById('apiKey').placeholder = `已保存（${cfg.api_key_masked}）· 留空则不修改`;
    }
    // 高级功能
    document.getElementById('streamEnabled').checked = cfg.stream_enabled || false;
    document.getElementById('thinkingEnabled').checked = cfg.thinking_enabled || false;
    if (cfg.thinking_budget_tokens) {
      document.getElementById('thinkingBudget').value = cfg.thinking_budget_tokens;
    }
    document.getElementById('thinkingBudgetRow').style.display = (cfg.thinking_enabled) ? '' : 'none';
    // 文章长度
    if (cfg.article_max_length) {
      document.getElementById('articleLength').value = cfg.article_max_length;
    }
    // 搜索配置
    if (cfg.search_provider) document.getElementById('searchProvider').value = cfg.search_provider;
    if (cfg.search_api_key_set) {
      document.getElementById('searchApiKey').placeholder = '已保存 · 留空则不修改';
    }
    if (cfg.search_base_url) document.getElementById('searchBaseUrl').value = cfg.search_base_url;
    // Firecrawl
    if (cfg.firecrawl_api_key_set) {
      document.getElementById('firecrawlApiKey').placeholder = '已保存 · 留空则不修改';
    }
    // Tavily
    if (cfg.tavily_api_key_set) {
      document.getElementById('tavilyApiKey').placeholder = '已保存 · 留空则不修改';
    }
    updateStatus(cfg.configured, cfg.configured ? '已配置大模型接口' : '尚未配置大模型接口');
  } catch (err) {
    updateStatus(false, '无法连接服务器');
  }
}

function updateStatus(ok, text) {
  const dot = document.getElementById('statusDot');
  const txt = document.getElementById('statusText');
  dot.className = 'status-dot ' + (ok ? 'ok' : 'bad');
  txt.textContent = text;
}

// ========================
// 保存配置
// ========================
async function saveConfig() {
  const apiKey = document.getElementById('apiKey').value.trim();
  const baseUrl = document.getElementById('baseUrl').value.trim();
  const model = document.getElementById('model').value.trim();
  const proxyUrl = document.getElementById('proxyUrl').value.trim();
  const temperature = parseFloat(document.getElementById('temperature').value);
  const streamEnabled = document.getElementById('streamEnabled').checked;
  const thinkingEnabled = document.getElementById('thinkingEnabled').checked;
  const thinkingBudget = parseInt(document.getElementById('thinkingBudget').value) || 2048;
  const articleLength = parseInt(document.getElementById('articleLength').value) || 2000;

  if (!baseUrl || !model) {
    showToast('⚠️ 请填写 Base URL 与模型名称', 'error');
    return;
  }

  const payload = {
    base_url: baseUrl,
    model: model,
    temperature: isNaN(temperature) ? 0.8 : temperature,
    stream_enabled: streamEnabled,
    thinking_enabled: thinkingEnabled,
    thinking_budget_tokens: thinkingBudget,
    article_max_length: articleLength,
    search_provider: document.getElementById('searchProvider').value,
    search_base_url: document.getElementById('searchBaseUrl').value.trim(),
  };
  if (proxyUrl) payload.proxy = proxyUrl;
  // 仅当用户填写了 key 才提交（否则保留已保存的）
  if (apiKey) payload.api_key = apiKey;
  const searchApiKey = document.getElementById('searchApiKey').value.trim();
  if (searchApiKey) payload.search_api_key = searchApiKey;
  // Firecrawl
  const firecrawlApiKey = document.getElementById('firecrawlApiKey').value.trim();
  if (firecrawlApiKey) payload.firecrawl_api_key = firecrawlApiKey;
  // Tavily
  const tavilyApiKey = document.getElementById('tavilyApiKey').value.trim();
  if (tavilyApiKey) payload.tavily_api_key = tavilyApiKey;

  try {
    const resp = await fetch('/api/llm-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (data.ok) {
      showToast('✅ 配置已保存', 'success');
      updateStatus(data.configured, '已配置大模型接口');
      document.getElementById('apiKey').value = '';
      document.getElementById('apiKey').placeholder = `已保存（****）· 留空则不修改`;
    } else {
      showToast('❌ 保存失败', 'error');
    }
  } catch (err) {
    showToast('❌ 保存失败：' + err.message, 'error');
  }
}

// ========================
// 测试连接
// ========================
async function testConnection() {
  const box = document.getElementById('testResult');
  box.style.display = 'block';
  box.className = 'test-result loading';
  box.innerHTML = '<div class="spinner"></div><p>正在测试连接...</p>';

  // 先用当前表单值保存配置，确保测试用的是用户刚填写的参数
  const apiKey = document.getElementById('apiKey').value.trim();
  const baseUrl = document.getElementById('baseUrl').value.trim();
  const model = document.getElementById('model').value.trim();
  const proxyUrl = document.getElementById('proxyUrl').value.trim();
  const temperature = parseFloat(document.getElementById('temperature').value);
  const streamEnabled = document.getElementById('streamEnabled').checked;
  const thinkingEnabled = document.getElementById('thinkingEnabled').checked;
  const thinkingBudget = parseInt(document.getElementById('thinkingBudget').value) || 2048;
  const articleLength = parseInt(document.getElementById('articleLength').value) || 2000;

  if (!baseUrl || !model) {
    box.className = 'test-result error';
    box.innerHTML = '<div class="test-icon">⚠️</div><p>请先填写 Base URL 与模型名称</p>';
    return;
  }

  const payload = {
    base_url: baseUrl,
    model: model,
    temperature: isNaN(temperature) ? 0.8 : temperature,
    stream_enabled: streamEnabled,
    thinking_enabled: thinkingEnabled,
    thinking_budget_tokens: thinkingBudget,
    article_max_length: articleLength,
    search_provider: document.getElementById('searchProvider').value,
    search_base_url: document.getElementById('searchBaseUrl').value.trim(),
  };
  if (proxyUrl) payload.proxy = proxyUrl;
  if (apiKey) payload.api_key = apiKey;
  const searchApiKey = document.getElementById('searchApiKey').value.trim();
  if (searchApiKey) payload.search_api_key = searchApiKey;
  const firecrawlApiKey = document.getElementById('firecrawlApiKey').value.trim();
  if (firecrawlApiKey) payload.firecrawl_api_key = firecrawlApiKey;

  try {
    await fetch('/api/llm-config', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });

    const resp = await fetch('/api/test-llm', { method: 'POST' });
    const data = await resp.json();
    if (data.ok) {
      box.className = 'test-result success';
      box.innerHTML = '<div class="test-icon">✅</div><p>' + escapeHTML(data.message) + '</p>';
      updateStatus(true, '连接成功');
    } else {
      box.className = 'test-result error';
      box.innerHTML = '<div class="test-icon">⚠️</div><p>' + escapeHTML(data.message) + '</p>';
      updateStatus(false, '连接失败');
    }
  } catch (err) {
    box.className = 'test-result error';
    box.innerHTML = '<div class="test-icon">⚠️</div><p>请求失败：' + escapeHTML(err.message) + '</p>';
  }
}

// ========================
// 工具函数
// ========================
function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function showToast(message, type = 'success') {
  const container = document.getElementById('toastContainer');
  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  container.appendChild(toast);
  setTimeout(() => toast.remove(), 3000);
}
