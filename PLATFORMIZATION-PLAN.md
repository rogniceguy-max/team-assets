# Team Assets 平台化审查与实施计划

## 目标与执行边界

目标是让团队通过稳定、公开的 REST/OpenAPI 契约开发独立前端；官方 Web 前端只是一个客户端。当前继续采用可信内网、无强制身份认证模式。CORS 只限制浏览器可读取的来源，不构成认证；无认证写接口不得转发到公网。

本计划以现有 `main` 为基线，在 `platform-api` 分支渐进实施，不更换技术栈，不触碰 NAS，不修改或覆盖正式数据库与素材。每一阶段单独验证、单独提交；旧 `/api` 与 MCP 客户端在兼容迁移期继续工作。

## 基线记录

- 开始 SHA：`b5aa9f9d3f3b75968215ce9f57e45a374ed636b1`
- 开发分支：`platform-api`，由干净的 `main` 创建。
- 初始验证：`python -m pytest -q`，110 项通过；有两条现存依赖警告。
- 运行环境限制：当前机器没有 Docker CLI，因此没有构建容器或验证 NAS 运行态。下文对 8092/8093/8094 的说明是依据仓库 Compose 与代码，不代表已连接实际 NAS。

### 实施进度

- 阶段 0：已完成，提交 `ac44bc3`。
- 阶段 1：已完成，提交 `183842a`。增加只读 Service/SQLite 模式、上传初始化保护及独立预览快照工具；当前分支的 8094 Compose 指向 `preview-data` 只读挂载。阶段测试 115 项通过。
- 阶段 2：已完成，提交待记录。增加精确 CORS Origin 白名单；阶段测试结果待提交前记录。
- 阶段 3–5：尚未开始。

## 当前能力与接口清单

### REST 路由

| 现有路径 | 能力与 Service | 作为公共 API 的缺口 |
|---|---|---|
| `GET /api/state` | `AssetService.state()` 全量工作区快照 | 不分页、不按项目过滤、暴露聚合内部形状；目前被官方前端当成主数据源，不适合作为新客户端的长期契约 |
| `POST /api/projects` | 创建项目 | 缺项目列表、详情、修改、删除/恢复与删除影响预览 |
| `POST/PATCH /api/assets`、`PATCH /api/assets/{id}` | 创建、修改资产；更新使用 `version` | 缺列表、搜索、详情、删除/恢复与删除预览 |
| `POST /api/variants`、`PATCH /api/variants/{id}` | 创建、修改变体；PATCH 可设定/清除定稿 | 缺列表、详情、删除/恢复与删除预览 |
| `POST /api/variants/{id}/materials`、`POST /api/assets/{id}/materials` | 多文件上传并调用 `add_materials` | 当前每次最多 20 个文件、合计 30 MB，读取时将内容放入内存；与 MCP 续传协议不一致 |
| `GET /api/assets/{id}/materials` | 资产素材分页查询 | 缺按变体查询；响应没有明确模型 |
| `DELETE /api/materials/{id}`、`POST /api/materials/{id}/restore` | 软删除与恢复素材 | 需要正式响应/错误契约 |
| `/api/candidates`、`/api/candidate-folders`、`/api/candidate-entries/reorder` | 候选库分页查询、上传、登记、编辑/禁用、移动/混排、文件夹、引用、高亮 | 缺正式版本前缀、响应模型；混排没有版本或 ETag 冲突保护；候选素材目前没有独立删除/恢复能力，不能在 API 中假造 |
| `GET /api/trash`、`GET /api/history` | 回收站与历史查询 | 返回结构宽泛且缺分页/筛选契约 |
| `GET /api/events` | SSE revision 更新通知 | 当前只用于同源连接；没有 CORS 与 Last-Event-ID/重连契约 |
| `GET /files/{id}` | 原图或缩略图 `FileResponse` | 位于 API 命名空间之外；URL、缓存、ETag 与媒体类型契约未正式说明 |

路由位于 `routers/`。现有请求模型在 `schemas/models.py`，但 23 个 OpenAPI 路径的成功响应均未声明响应模型，错误响应也未完整进入 OpenAPI。

### MCP 独有或流程不同的能力

MCP 工具清单中有以下尚无等价 REST 操作的业务能力：

- 项目列表/修改；资产搜索/详情；变体详情。
- 项目、资产、变体删除影响预览、级联删除和恢复。
- 项目素材覆盖报告 `audit_project_asset_coverage`。
- `plan_project_import` 与 `apply_project_import` 项目清单导入。
- 按变体查询素材、素材预览的 MCP Image 响应。
- 大文件上传会话：申请、查询 offset、PUT 分块、完成、取消；原始 HTTP 上传路由由 `UploadManager` 挂在 MCP ASGI app 下。
- `commit_intake` 原子入库。
- MCP 客户端能力/Skill manifest 与客户端 zip 下载属于传输/客户端维护能力，不是资产业务 API。

当前 REST 和 MCP 的重叠业务大多调用同一 `AssetService` 方法及同一 SQLite 数据模型，包括实体更新与版本冲突、素材、候选库、回收站、历史和状态查询。两端通常是不同进程/不同 Service 实例，共享 `DATA_DIR`，不是共享内存对象。入参解析、上传大小/协议及错误封装不同；MCP 的批量导入和大文件上传流程尚未开放给 REST。

## 现有耦合与不适合稳定对外的入口

- `/api/state` 是不分页的完整快照，字段直接来自服务层记录字典；候选项目集还受 active/禁用文件夹规则影响。适合现有官方客户端刷新，不应要求新前端依赖它。
- `web-dock/api-client.js` 固定请求同源 `/api`，应用直接固定 `EventSource('/api/events')`。其可继续作为官方客户端实现，但不是通用 TypeScript/JS client。
- 业务 REST 与 MCP 已复用服务规则，但多项服务操作只通过 MCP 暴露；简单 multipart REST 与 MCP 分块上传亦是两套不同流程。
- 基线 REST 中间件只允许 Host 同源写请求；不带 `Origin` 的客户端仍可写，不能作为身份/网络安全边界。阶段 2 后，当前分支可通过 `TEAM_ASSETS_CORS_ORIGINS` 设置精确浏览器白名单；默认不信任额外来源，也不接受通配符。
- `core/db.py` 在服务初始化时创建数据目录、设置 WAL、建表、检查/变更列、执行数据归一化、迁移和建索引。迁移并非全部由统一版本表管理。因此只读预览不能复用当前的普通启动流程。
- 多个 REST 操作没有 `response_model`，成功输出主要是 Service 返回的原始字典；`/docs` 和 `/openapi.json` 可用但现状不足以让员工可靠生成客户端。

## 8092、8093、8094 基线配置行为

以下表格描述开始 SHA `b5aa9f9` 时仓库中的配置。阶段 1 后，当前分支的 8094 已改为从 `${PREVIEW_DATA_DIR:-./preview-data}` 只读挂载独立快照，并启用 `TEAM_ASSETS_READ_ONLY=1`；没有实际运行容器验证。

| 入口 | Compose 与应用行为 | 数据和认证边界 |
|---|---|---|
| 8092 | 主 Compose 的 Web/FastAPI，挂载 `web-dock` 到 `/app/web`，提供 REST 与官方前端 | `./data:/data` 可写；没有员工登录；同源写入限制不是认证 |
| 8093 | 独立 MCP 进程，监听 8001 后映射到 8093 | 同样挂载 `./data:/data` 可写；默认 `MCP_AUTH_MODE=none`，可选 Bearer 兼容能力保留 |
| 8094 | 预览 Compose 使用 host network 启动 FastAPI 8094，挂载 `web-dock-preview`，并设置 `ENABLE_MCP_HTTP=1` | 当前挂载同一 `./data:/data` 且可写；MCP 默认无认证；Web 与 MCP 分别构造 Service；上传管理器启动时还会建表并清理过期会话。因此它能修改正式数据，绝非只读预览 |

Docker Compose 未把端口绑定到 loopback，默认会在宿主机所有接口发布。无认证入口只能放在可信局域网或已有 VPN 内，禁止直接转发公网。当前没有访问实际 NAS，无法确认部署中的 Compose 是否与仓库一致。

## 数据与迁移兼容风险

- SQLite 位于共享 `/data/database/assets.sqlite3`，WAL 用于同一 NAS 文件系统上的 Web/MCP 进程并发；不要放 SMB/NFS。生产数据目录及原图/缩略图不得在本次改造中迁移或覆盖。
- 现有实体更新含 `version`，服务层在事务内检测冲突并返回 409；候选混排目前没有相同版本保护。
- 新旧 schema 兼容依赖启动时检测列与迁移；已有测试覆盖部分旧资产 schema，但不是完整历史备份矩阵。
- 只读预览若仅把运行中的数据库主文件拷出，会丢 WAL 中的提交，不能作为一致快照。推荐通过 SQLite backup API 在线生成独立数据库快照，再复制不可变原图/缩略图目录；预览只读挂载快照，启动时跳过 schema 初始化/迁移和上传清理。
- 所有 schema 变化必须新增兼容迁移和旧数据库测试；不能为只读预览执行迁移，也不能让 8094 启动时初始化正式 `/data`。

## 渐进实施阶段

### 阶段 0：能力盘点与方案（本提交）

形成以上接口/部署清单和兼容边界；未触碰数据与部署。

### 阶段 1：服务装配与 8094 只读隔离

- 从 `server.py`/`mcp_server.py` 双向导入中拆出稳定服务门面和应用装配；Web/MCP app factory 支持显式注入 Service。
- 加服务端只读模式：SQLite 使用只读连接和 `query_only`，禁止 schema 初始化/迁移；HTTP 写入、MCP 写工具、上传分块、上传完成/取消和启动清理都必须被服务端拒绝或关闭。
- 8094 改用独立一致快照目录并以只读卷挂载；不自动指向生产 `./data`。保留预览查询、静态前端、图片读取和 SSE。
- 测试数据库、Service、REST、MCP、上传、启动行为都不能写入或清理。保持 8092/8093 可写行为。

### 阶段 2：可信 Origin 白名单与跨源方案

- 增加显式 `TEAM_ASSETS_CORS_ORIGINS` 配置；默认不允许额外跨源 Origin，不使用 `*`。
- 正确支持合法 Origin 的 GET、OPTIONS、REST 写入、上传自定义头与 SSE；非法浏览器 Origin 不获 CORS 授权，并且写操作被服务端 Origin 规则拒绝。
- 对没有 Origin 的内网/MCP/脚本客户端维持无认证兼容，但文档明确这依赖网络隔离；CORS 不等于身份验证。
- 提供 Vite 本地代理和同源反代说明，不要求员工每次修改后端。

### 阶段 3：正式 `/api/v1` 合同与能力补齐

- 新增正式 `/api/v1` 路由，同时保留全部现有 `/api` 与 `/files` 路径作为兼容别名。
- 按 REST/MCP 能力矩阵逐项补齐项目/资产/变体列表、详情、修改、删除预览、删除/恢复；素材按变体查询、设定/取消定稿；完整分页候选库、文件夹、回收站和历史。
- 把 MCP 大文件上传会话、文件块 REST 上传和原子 intake/项目导入流程开放为版本化 REST API；调用现有 Services/UploadManager，不能复制业务规则。
- 给公开 API 使用 Pydantic 请求及响应模型、分页约束、稳定 ID、明确状态码和统一可文档化错误体；保留现有版本冲突和幂等语义。排序/批量写入需要明确并发冲突方式。
- `/api/state` 保持兼容但标记为旧版快照入口；新示例前端不依赖它。

### 阶段 4：OpenAPI 与员工指南

- 完善 OpenAPI 3.x tags、摘要、成功响应、错误响应、分页/筛选约束及示例；保留 `/docs` 与 `/openapi.json`。
- 编写中文员工指南：代理/反代、读写、上传、下载/预览、409 与幂等重试、SSE、版本兼容/弃用、安全边界。
- 提供独立 TypeScript API Client 示例，并用 OpenAPI 合同测试防止路由与模型漂移。

### 阶段 5：独立参考前端与跨端验证

- 新增 `examples/standalone-frontend/` 原生 HTML/JS 参考客户端，仅调用 `/api/v1`，不加载 `web/`、`web-dock/` 或 Python 源码。
- 支持项目选择、资产浏览/预览、创建/修改资产、上传图片、SSE 更新和 409 冲突恢复。
- 以本地代理和真实跨来源方式各验证一次；A 端创建/修改后 B 端收到 SSE 并刷新，B 端也可以修改且 A 端收到变化。
- 另外用现有官方前端验证旧路由兼容，不声称单元测试等同 NAS、Docker 或所有浏览器验收。

## 验收矩阵与回滚

每个阶段单独运行相关 pytest，再运行完整套件。最终需覆盖：旧路由不回归、新 API/OpenAPI 合同、REST/MCP 读写同一数据与共用业务规则、可信/不可信 Origin、OPTIONS/SSE/上传、幂等与 409、只读启动和拒绝所有写路径、快照完整性、数据库旧版迁移以及独立前端跨来源协作。

任何阶段失败时，仅回退该阶段提交；数据库迁移如已用于实际环境，回滚代码前必须确认迁移是否前向兼容。本工作分支不部署到 NAS。验证成功后再单独评审是否推送/部署，部署仍需独立授权。

## 本阶段实际验证限制

已在本地只读检查源代码和 Compose，并运行现有完整 pytest。Docker CLI 不可用，没有构建/启动容器；没有访问 NAS；没有进行真实浏览器跨源/SSE 验收。上述均列为后续显式验收项。
