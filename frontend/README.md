# Vue 前端

这是 AI 智能记账助手的前端项目，使用 Vue 3 和 Vite 构建，提供注册登录、聊天记账、收支统计及账单管理界面。

完整的功能说明、截图、技术架构和后端配置请查看[项目首页](../README.md)。

## 本地启动

在 `frontend/` 目录执行：

```bash
npm ci
cp .env.example .env
npm run dev
```

在 `.env` 中设置 `VITE_API_BASE_URL`，默认连接 `http://127.0.0.1:8000`。使用前需按照项目首页说明启动后端。

## 构建

```bash
npm run build
```

构建产物位于 `dist/`。前端环境变量会进入浏览器，请勿将任何真实密钥放入 `VITE_*` 变量。
