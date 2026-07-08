# 🚀 TrendTweet · 热点爆款推文生成器

实时聚合全网热点资讯，智能生成爆款推文。

## 功能特性

- 📡 **多源热点聚合** — Hacker News、Reddit、微博热搜
- ✍️ **4种推文风格** — 专业深度、幽默风趣、悬念吸引、情感共鸣
- 🎨 **Linear/Vercel 风格暗色主题** — 现代化 UI 设计
- 📋 **一键复制** — 快速保存生成的推文
- 🔄 **实时刷新** — 随时获取最新热点
- 📱 **响应式设计** — 适配桌面和移动端

## 快速开始

### 1. 安装依赖

```bash
cd trending-tweet-generator
pip install -r requirements.txt
```

### 2. 启动服务

```bash
python backend/app.py
```

### 3. 打开浏览器

访问 `http://localhost:5000`

## 项目结构

```
trending-tweet-generator/
├── backend/
│   └── app.py              # Flask 后端 API
├── frontend/
│   ├── index.html          # 主页面
│   ├── style.css           # 样式表
│   └── app.js              # 前端交互逻辑
├── requirements.txt        # Python 依赖
└── README.md
```

## API 接口

### GET /api/hotspots
聚合多源热点数据，返回按热度排序的热点列表。

### POST /api/generate-tweet
生成爆款推文。

**请求体：**
```json
{
  "hotspot_id": 0,
  "style": "professional",
  "hotspots": [...]
}
```

**风格选项：**
- `professional` — 专业深度
- `humorous` — 幽默风趣
- `suspense` — 悬念吸引
- `emotional` — 情感共鸣

## 技术栈

- **后端：** Python 3 + Flask
- **前端：** 原生 HTML/CSS/JavaScript
- **数据源：** Hacker News API, Reddit API, 微博热搜(模拟)

## License

MIT
