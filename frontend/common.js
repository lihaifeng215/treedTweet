/**
 * TrendArticle · 共享工具模块
 * 消除 app.js / library.js / workflow.js 之间的代码重复
 */

/* ---------- 数据源映射 ---------- */
function getContentType(source) {
  const map = {
    'Hacker News': 'tech', 'GitHub Trending': 'tech', '少数派': 'tech',
    '量子位': 'tech', 'V2EX': 'tech', '机器之心': 'tech',
    '36氪': 'business', '虎嗅': 'business',
    '百度热搜': 'trending', '头条热榜': 'trending', '微博热搜': 'trending', '网易': 'trending',
    'B站热门': 'entertainment', '抖音热榜': 'entertainment',
    '知乎热榜': 'knowledge',
  };
  return map[source] || '';
}

function getCategoryName(category) {
  const map = { 'tech': '科技前沿', 'business': '商业财经', 'trending': '热点资讯', 'entertainment': '娱乐视频', 'knowledge': '知识社区' };
  return map[category] || '';
}

function getCategoryIcon(category) {
  const map = { 'tech': '🔬', 'business': '📈', 'trending': '🔥', 'entertainment': '🎬', 'knowledge': '📚' };
  return map[category] || '';
}

function getSourceClass(source) {
  const map = {
    'Hacker News': 'source-hackernews', 'B站热门': 'source-bilibili',
    '百度热搜': 'source-baidu', '头条热榜': 'source-toutiao',
    '抖音热榜': 'source-douyin', 'GitHub Trending': 'source-github',
    '微博热搜': 'source-weibo',
  };
  return map[source] || '';
}

function getSourceIcon(source) {
  const map = {
    'Hacker News': '🟠', 'B站热门': '🅱️', '百度热搜': '🔍', '头条热榜': '📰',
    '抖音热榜': '🎵', 'GitHub Trending': '🐙', '微博热搜': '📢',
  };
  return map[source] || '📰';
}

/* ---------- 格式化函数 ---------- */
function formatTime(isoStr) {
  if (!isoStr) return '--';
  const d = new Date(isoStr);
  return d.toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
}

function formatRelativeTime(isoStr) {
  if (!isoStr) return '';
  const now = new Date();
  const d = new Date(isoStr);
  const diffSec = Math.floor((now - d) / 1000);
  if (diffSec < 60) return '刚刚';
  const diffMin = Math.floor(diffSec / 60);
  if (diffMin < 60) return `${diffMin}分钟前`;
  const diffHour = Math.floor(diffMin / 60);
  if (diffHour < 24) return `${diffHour}小时前`;
  const diffDay = Math.floor(diffHour / 24);
  if (diffDay < 30) return `${diffDay}天前`;
  return d.toLocaleDateString('zh-CN', { month: 'short', day: 'numeric' });
}

function formatEngagement(num) {
  if (!num) return '0';
  if (num >= 10000) return (num / 10000).toFixed(1) + 'w';
  if (num >= 1000) return (num / 1000).toFixed(1) + 'k';
  return String(num);
}

function estimateWordCount(text) {
  const chineseChars = (text.match(/[\u4e00-\u9fff]/g) || []).length;
  const englishWords = (text.match(/[a-zA-Z]+/g) || []).length;
  return chineseChars + englishWords;
}

/* ---------- HTML 安全转义 ---------- */
function escapeHTML(str) {
  const div = document.createElement('div');
  div.textContent = str;
  return div.innerHTML;
}

function escapeAttr(str) {
  return String(str).replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/"/g, '&quot;');
}

/* ---------- 剪贴板 ---------- */
function copyTitle(title) {
  navigator.clipboard.writeText(title).then(() => {
    showToast('📋 标题已复制', 'success');
  }).catch(() => {
    fallbackCopy(title);
  });
}

function fallbackCopy(text) {
  const ta = document.createElement('textarea');
  ta.value = text;
  ta.style.position = 'fixed'; ta.style.opacity = '0';
  document.body.appendChild(ta); ta.select();
  try { document.execCommand('copy'); } catch(e) {}
  document.body.removeChild(ta);
  showToast('📋 已复制', 'success');
}

/* ---------- Toast 通知（统一容器模式）--------- */
function showToast(message, type = 'success') {
  let container = document.getElementById('toastContainer');
  if (!container) {
    container = document.createElement('div');
    container.id = 'toastContainer';
    container.className = 'toast-container';
    document.body.appendChild(container);
  }

  const toast = document.createElement('div');
  toast.className = `toast-item ${type}`;
  toast.textContent = message;
  container.appendChild(toast);

  requestAnimationFrame(() => toast.classList.add('show'));

  setTimeout(() => {
    toast.classList.remove('show');
    setTimeout(() => toast.remove(), 300);
  }, 3000);
}
