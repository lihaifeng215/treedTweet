# TrendTweet 项目总结

## 项目概述
已完成热点爆款推文生成器网站开发。

## 技术栈
- **后端**: Python Flask
- **前端**: 原生 HTML/CSS/JavaScript
- **设计风格**: Linear/Vercel 暗色主题

## 核心功能
1. **多源热点聚合**
   - Hacker News 热门技术新闻
   - Reddit 社区热门帖子
   - 微博热搜话题

2. **智能推文生成**
   - 4种风格：专业深度、幽默风趣、悬念吸引、情感共鸣
   - 一键复制推文内容
   - 爆款小贴士建议

## 部署信息
- **本地服务**: http://localhost:5000
- **项目路径**: /home/lihaifeng/trending-tweet-generator/
- **启动命令**: `python backend/app.py`

## 项目结构
```
trending-tweet-generator/
├── backend/app.py          # Flask 后端 API
├── frontend/
│   ├── index.html          # 主页面
│   ├── style.css           # 样式表
│   └── app.js              # 前端逻辑
├── requirements.txt        # Python 依赖
└── README.md
```

## 下次使用
1. 启动服务: `cd /home/lihaifeng/trending-tweet-generator && python backend/app.py`
2. 访问: http://localhost:5000
3. 点击"刷新热点"获取最新资讯
4. 选择热点点击"生成推文"
5. 选择风格并复制推文内容
