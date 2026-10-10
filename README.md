# 团队资产库

MCP 已升级到 v2：新增删除恢复、精确查询、大文件上传会话和批量入库。请优先阅读 [MCP v2 使用说明](MCP-v2.md)。下文30MB限制仅适用于旧Base64/网页上传通道；新MCP上传会话默认不设原文件字节上限。

NAS 部署的共享图片资产工作台，浏览器即开即用。网页和 MCP 共用数据服务，无需用户账号。

## 本地运行

在本目录执行（Python 3.12 或更新）：

```powershell
python -m pip install -r requirements.txt
python -m uvicorn server:app --host 127.0.0.1 --port 8092
```

访问 http://localhost:8092 。首次启动只有一个默认项目，数据存放在本目录 data 下。可用 DATA_DIR 环境变量更改。API 文档位于 /docs。

员工独立前端请从 [`docs/API-v1.md`](docs/API-v1.md) 开始，并可直接使用 `/openapi.json` 生成客户端；仓库还提供 [`examples/typescript-client.ts`](examples/typescript-client.ts) 与纯前端的 [`examples/standalone-frontend/`](examples/standalone-frontend/)。

独立前端开发时，推荐先使用本地开发服务器代理 `/api` 和 `/files` 到 `http://localhost:8092`，这样开发端口不需要额外跨域配置。若前端必须跨来源直连后端，在 `.env` 设置精确来源，例如 `TEAM_ASSETS_CORS_ORIGINS=http://localhost:5173`；多个来源用逗号分隔。不能设置 `*`。

示例 Vite 代理配置：

```js
export default {
  server: {
    proxy: {
      '/api': 'http://localhost:8092',
      '/files': 'http://localhost:8092',
    },
  },
};
```

## NAS Docker

将整个 team-assets 文件夹复制到 NAS 本机存储目录后直接执行（默认无认证）：

```sh
docker compose up -d --build
```

网页：http://NAS地址:8092

MCP：http://NAS地址:8093/mcp

如果只需要网页，可执行 `docker compose up -d --build web`。端口可通过环境变量
`WEB_PORT`/`MCP_PORT` 调整；默认分别为 8092/8093。`data` 目录映射到两个容器的
`/data`，更新镜像不会丢失素材。

8094 预览入口只挂载独立只读快照。需要创建快照时，指定一个尚不存在的目标目录：

```sh
python scripts/create_preview_snapshot.py ./data ./preview-data-2026-10-10
```

然后在 `.env` 中设置 `PREVIEW_DATA_DIR=./preview-data-2026-10-10`，再运行
`docker compose -f docker-compose.preview.yml up -d --build`。该快照工具通过 SQLite backup API 读取一致数据库，并复制原图/缩略图；它不修改源数据。每次刷新请选一个新的目标目录，避免覆盖旧快照。

网页和 MCP 当前没有强制身份认证，也不区分员工身份或权限；能访问端口的客户端拥有相同业务操作能力。CORS 白名单只限制浏览器跨源读取，不能防止脚本、MCP 或其他非浏览器客户端访问，也不能替代认证。服务只能放在受控内网或 VPN 后，必须用防火墙阻止公网访问；严禁将无认证写接口直接转发到公网。远程入口应使用 HTTPS。不要把 NAS 数据库放在 SMB/NFS 网络挂载上。

## AI / MCP 连接

在支持 Streamable HTTP 的 MCP 客户端中填写：

- URL：`http://NAS地址:8093/mcp`
- 请求头：无（当前 NAS 默认是可信局域网无认证模式）
- 传输：Streamable HTTP

具体客户端配置格式不同；这里提供的是连接参数，不是某个客户端的专用配置文件。
若部署到不可信网络，设置 `MCP_AUTH_MODE=bearer` 和 `TEAM_ASSETS_MCP_TOKEN` 可恢复
Bearer 认证。

本地单独启动 MCP（在同一个 DATA_DIR 下）：

```powershell
python mcp_server.py
```

默认本地 MCP 监听 8001，与 Docker 映射后的 NAS 端口 8093 不同。

建议给 AI 的使用约定：

> 先查询资产库并确认目标项目、资产、变体。生图后展示图片和拟填写的集数、设定说明，询问用户是否加入候选素材；用户确认后再提交。不要根据文件名猜测同名对象。只有用户明确要求时才设置定稿。重试同一批上传时复用请求标识。

首版通过 base64 上传可导出的图片内容，不会直接读取 AI 电脑的路径，也不会自动连接生图平台。每次上传总计不超过 30MB。服务工具描述提醒确认，但对话确认由 AI 客户端执行，服务器不会伪称已经验证用户确认。

## 使用

1. 创建项目和资产，为人物/场景/道具建立变体。
2. 将图片拖入对应变体，或通过上传选择目标。
3. 点击图片预览、下载、设为定稿或移入回收站。
4. 在详情中修改集数与设定，其他页面自动收到更新。
5. 在回收站恢复误删素材，最近更新查看操作记录。

支持 PNG、JPEG、WebP、GIF；原文件保留，缩略图用于列表。首版不支持视频、PSD、模型文件预览，不包含生图引擎。

## 数据与备份

数据库保存项目、资产、变体、素材引用、文件信息和修改记录；原图与缩略图分目录持久化。删除素材是软删除，不立即清除原图。SQLite WAL 支持单 NAS 内网页与 MCP 两个进程共享数据库。

一致性备份：停止两个服务，复制完整 data 目录，再启动。恢复时也停止两个服务，用同一次备份的完整目录恢复。不要仅复制运行中的数据库主文件而漏掉 WAL 与原图。

```sh
docker compose stop
# 使用 NAS 文件管理器复制完整 data 目录到备份位置
docker compose start
```

## 验证

```powershell
python -m pytest -q
node --check web-dock/app.js
node --check examples/standalone-frontend/app.js
```

Docker 构建需在安装 Docker 的 NAS 或电脑上运行。本项目独立于上级目录的其他应用，不会修改其服务与数据。

更完整的交互和存储取舍见 DESIGN.md。
