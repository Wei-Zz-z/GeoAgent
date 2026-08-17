# GeoAgent

通过自然语言对话完成地理空间分析的智能体应用：用户在会话窗口输入自然语言，
Agent 自主调用空间分析工具，并把结果（GeoJSON 图层、表格等）**渲染在会话窗口内**。

开发规范见 [AGENTS.md](AGENTS.md)。

## 当前进度（2026-08-17）

- 后端骨架（FastAPI + WebSocket + 多模型切换 + 工具注册/执行 + Agent 图编排）已搭建，
  13 个 pytest 用例通过，已用阿里千问真实跑通"加载数据 → 缓冲区 → GeoJSON"链路。
- 前端骨架（Vue3 + OpenLayers）已搭建：左侧会话列表、右侧会话窗口、流式工具卡片、
  内嵌地图/表格可视化、会话级模型切换。

## 快速开始

```bash
# 后端
cd backend
uv sync
uv run --env-file .env uvicorn geoagent.server.app:app --reload --port 8000

# 前端（另开终端）
cd frontend
npm install
npm run dev          # 打开 http://localhost:5173
```

环境变量参考 `backend/.env.example`；API key 一律走环境变量，禁止入库。

## 目录

```text
GeoAgent/
├── backend/    # Python + FastAPI 后端（uv 管理）
└── frontend/   # Vue3 + Vite + OpenLayers 前端
```
