# Team Assets 员工 API 指南

这份文档和运行中的 OpenAPI 规范可以直接交给 Codex、Gemini 或其他 AI，用来生成独立前端。新客户端只需调用公开 HTTP API，不需要阅读官方前端 JavaScript、Python 服务或数据库结构。

## 服务地址与安全边界

- NAS 默认地址：`http://NAS地址:8092`
- API 根路径：`/api/v1`
- OpenAPI 规范：`http://NAS地址:8092/openapi.json`
- 浏览器接口文档：`http://NAS地址:8092/docs`
- MCP：`http://NAS地址:8093/mcp`，继续供 AI 工具使用。

团队内网模式不要求员工登录，不区分个人身份或权限。能访问服务的客户端都能执行相同的业务操作。CORS 白名单只约束浏览器的跨来源读取，并不是认证；命令行程序、MCP 客户端或其他非浏览器客户端可以不发送 `Origin`。因此服务必须放在可信内网或 VPN 后，并通过防火墙限制访问；**严禁把无认证写接口直接开放到公网**。如果部署网络不再可信，应另行规划认证和网络隔离，不要把 CORS 当作替代品。

同源前端无需额外配置。跨来源网页只有在后端显式配置其精确 Origin 后才能读取 API，例如 Docker Compose 的 `.env`：

```dotenv
TEAM_ASSETS_CORS_ORIGINS=http://localhost:5173,https://assets-ui.intra.example
```

Origin 必须包含协议和端口（如有），不含路径；不能配置 `*`。直接运行 Python 时，要把变量设置在启动后端的进程环境中。员工本地开发也可以用 Vite 等工具代理 `/api` 和 `/files` 到 NAS，以同源方式开发。预检 `OPTIONS`、SSE 和分块上传所需的 `X-Upload-Token`、`Idempotency-Key` 等请求头会按同一白名单处理。

## 基本约定

- JSON 请求使用 `Content-Type: application/json`。上传图片时传 `FormData`，不要手动设置 multipart 的 `Content-Type`，浏览器会添加 boundary。
- 成功的创建请求返回 `201`，查询/更新/恢复通常返回 `200`。
- 列表默认 `limit=50&offset=0`，`limit` 范围是 1–200；结果包含 `total`、`limit` 和 `offset`。
- 可排序的列表提供 `sort_by` 和 `order=asc|desc`。排序字段由每个端点的 OpenAPI 参数枚举限定。
- 更新项目、资产、变体、候选素材或候选文件夹时，必须传当前 `version`。变体的 `episodes` 使用规范字符串，例如 `all`、`3`、`3-10`、`>35`；解释写入 `description`。版本化集数会同时返回结构化的 `episode_scope`。
- `episode_usages` 是后端审计字段，每项形如 `{ "episodes": "3-5", "mode": "voice", "note": "门外声音" }`，不代替时间线筛选字段。
- 已废弃的 `/api/...` 和 `/files/...` 路径仍为现有客户端保留；新项目只使用 `/api/v1/...`。旧路径在 OpenAPI 中标记为 deprecated。

## 项目、资产与变体

| 方法与路径 | 用途 |
|---|---|
| `GET /api/v1/projects?limit=50&offset=0&sort_by=position&order=asc` | 分页列出项目；`sort_by` 可选 `position` 或 `name` |
| `POST /api/v1/projects` | 创建项目，JSON `{ "name": "项目名", "description": "说明" }` |
| `GET /api/v1/projects/{project_id}`、`PATCH /api/v1/projects/{project_id}` | 查询或修改项目；更新请求带 `version` |
| `GET /api/v1/projects/{project_id}/assets` | 在项目内搜索/筛选/分页资产；支持 `query`、`category`、`limit`、`offset`、`sort_by`、`order` |
| `GET /api/v1/assets` | 全局资产搜索/筛选/分页；支持 `query`、`project_id`、`category`、`sort_by`、`order` |
| `POST /api/v1/assets`、`GET /api/v1/assets/{asset_id}`、`PATCH /api/v1/assets/{asset_id}` | 创建、查看（含变体列表）、修改资产 |
| `GET /api/v1/variants?asset_id=...`、`POST /api/v1/variants`、`GET/PATCH /api/v1/variants/{variant_id}` | 分页列出、创建、查看、修改变体 |
| `PATCH /api/v1/variants/{variant_id}/final-material` | 设定或取消定稿；提交 `{ "version": 2, "material_id": "素材ID" }`，取消时 `material_id` 为 `null` |
| `POST /api/v1/projects/{id}/delete-preview`、`POST /api/v1/assets/{id}/delete-preview`、`POST /api/v1/variants/{id}/delete-preview` | 预览级联删除的影响；JSON 为 `{ "version": 1 }`，不会更改数据 |
| `DELETE /api/v1/projects/{id}`、`DELETE /api/v1/assets/{id}`、`DELETE /api/v1/variants/{id}` | 按版本执行软删除；DELETE 请求 JSON 为 `{ "version": 1 }` |
| `POST /api/v1/{projects,assets,variants}/{id}/restore` | 从回收站恢复项目、资产或变体 |
| `GET /api/v1/projects/{project_id}/coverage?include_disabled=false` | 生成项目资产覆盖报告 |

更新示例：

```http
PATCH /api/v1/assets/asset-123
Content-Type: application/json

{"version": 3, "name": "女主", "category": "人物", "role": "主角"}
```

定稿是素材池内素材与变体之间的引用。同一资产池素材可以被同资产下多个变体分别选为定稿。

## 素材与候选库

| 方法与路径 | 用途 |
|---|---|
| `GET /api/v1/assets/{id}/materials` | 分页查询资产共享素材池 |
| `GET /api/v1/variants/{id}/materials` | 按变体定位其所属资产的共享素材池；返回内容仍是该资产的全部候选素材 |
| `POST /api/v1/assets/{id}/materials`、`POST /api/v1/variants/{id}/materials` | 小批次 multipart 图片上传；每批最多 20 个文件且总计不超过 30 MiB。大文件使用下文的续传上传 |
| `GET /api/v1/materials/{id}`、`DELETE /api/v1/materials/{id}`、`POST /api/v1/materials/{id}/restore` | 查看、移入回收站和恢复素材 |
| `GET /api/v1/candidates?project_id=...&status=active&query=...` | 分页查询候选素材；`status` 为 `active`、`disabled` 或 `all`，搜索只匹配文件名 |
| `POST /api/v1/candidates` | 以 multipart 上传候选图片或视频，可传多个 `files`、`project_id`、`category`，并可设置 `Idempotency-Key` |
| `POST /api/v1/candidates/from-file` | 把续传上传完成后的 `file_id` 登记到候选库，可设置文件名、分类与目录 |
| `GET /api/v1/candidates/{id}`、`PATCH /api/v1/candidates/{id}` | 查询、重命名、改分类或禁用/恢复候选素材；更新带 `version` |
| `POST /api/v1/candidates/{id}/move` | 移动候选素材到已有目录或根目录；请求带 `version` |
| `GET /api/v1/candidate-folders?project_id=...&status=all`、`PATCH /api/v1/candidate-folders/{id}` | 查询、重命名或禁用目录；更新带 `version` |
| `POST /api/v1/candidate-entries/reorder` | 提交同一目录下的文件与文件夹混合排序；需带最新 `expected_revision` |
| `POST /api/v1/candidates/{id}/highlight?seconds=60` | 临时高亮候选素材，时间为 1–300 秒 |
| `POST/DELETE /api/v1/candidates/{id}/references` | 创建或删除候选素材与资产/变体之间的引用 |

当前业务没有候选素材删除操作；候选素材可禁用和恢复。因此 API 没有伪造一个候选素材删除路由。文件和图片内容通过 stable `file_id` 读取，响应中不会返回服务器的物理存储路径。

## 大文件续传和原子入库

小图片可直接用 multipart 资产上传。大图片或视频用续传会话，默认每块最多 8 MiB；默认不设原始文件总大小上限，实际受可用磁盘、媒体解码限制及管理员设置的 `MAX_FILE_BYTES` 约束。若设置 `MAX_FILE_BYTES=0`，代表不限制单文件字节数。

1. `POST /api/v1/uploads`，JSON 示例：

```json
{"filename":"render.png","size":7340032,"sha256":"可选的64位SHA-256","idempotency_key":"同一批重试时复用"}
```

响应包含 `id`、`upload_token`、`upload_url`、`offset` 和 `chunk_size`。这个随机 token 仅授权访问这一个上传会话，不是用户账号或全局 API Key。不要把它写进日志或分享给无关客户端。

2. 向 `upload_url?offset=已接收字节数` 发送二进制块：

```http
PUT /api/v1/uploads/{upload_id}?offset=0
Content-Type: application/octet-stream
X-Upload-Token: <本会话 upload_token>
```

3. 网络中断后，先用同一 `upload_url` 加 `X-Upload-Token` 查询状态，根据返回的 `offset` 继续；不要猜测服务器是否收到了最后一块。完成前，对应会话的查询、续传、完成和取消都要求该会话 token。

4. `POST /api/v1/uploads/{upload_id}/complete` 验证文件、生成缩略图并返回 `file_id`；然后使用 `/api/v1/intake/commit` 将文件原子加入已有资产/变体或新建资产/变体。例如：

```json
{
  "project_id": "project-id",
  "asset_id": "existing-asset-id",
  "variants": [{"name":"新状态","episodes":"3-10","file_ids":["completed-file-id"]}],
  "idempotency_key": "intake-operation-1"
}
```

`asset_id` 与 `asset` 必须且只能传一个。已有变体用 `variant_id`，新建变体用 `name`。一次最多 100 个变体；同一个 `idempotency_key` 重试相同内容会返回原结果，重试不同内容返回 409。入库事务全部成功或全部回滚，不会自动设定稿。

上传到候选库时，可在 `complete` 后调用 `/api/v1/candidates/from-file` 登记 `file_id`，不会重新传输文件字节。

## 批量导入、回收站与历史

- `POST /api/v1/project-imports/plan`：提交 `{project_id, assets, on_name_conflict}` 校验导入计划，不写数据。`on_name_conflict` 为 `error` 或 `reuse`。
- `POST /api/v1/project-imports/apply`：提交相同字段并增加 `idempotency_key`，在一个事务中写入资产、变体、素材及候选引用。
- `GET /api/v1/trash?entity_type=asset&limit=50&offset=0`：分页查回收站。`entity_type` 可选 `project`、`asset`、`variant`、`material`；省略时返回混合分页。
- `GET /api/v1/history?limit=50&offset=0&action=update_asset`：分页查操作记录，可按 action 筛选。

## 文件预览与下载

`GET /api/v1/files/{file_id}` 返回原始媒体字节。用 `?thumbnail=true` 返回 JPEG 缩略图，用 `?download=true` 返回带下载文件名的附件。资源的 `url` 和 `thumbnail_url` 是相对当前 API 主机的路径：

```ts
const imageUrl = new URL(material.thumbnail_url, API_BASE_URL);
```

v1 响应中的 `url` 和 `thumbnail_url` 指向 `/api/v1/files/{file_id}`；旧数据/兼容接口仍可能返回 `/files/{file_id}`。文件记录不可变且使用稳定 ID；素材移入回收站不会立即删除底层文件，恢复/下载能力遵循素材业务状态。

## 实时更新（SSE）

连接 `GET /api/v1/events`，响应为 `text/event-stream`，每次变化发送：

```text
data: {"revision":42}
```

页面首次加载时先调用常规列表接口；收到 revision 后重新拉取当前页面数据。浏览器 `EventSource` 会自动重连；该流只通知 revision，不重放每一条历史，也不包含变更对象。重连后再次查询资源以重新同步。`GET /api/v1/revision` 可读取当前 revision，用于候选混排的 `expected_revision`。

## 错误与并发更新

错误主体统一位于 `detail`：

```json
{"detail":{"code":"VERSION_CONFLICT","message":"内容已更新，请重新读取","current":{"id":"...","version":4}}}
```

| 状态码 | 处理方式 |
|---|---|
| `400` | 请求格式不正确；根据 `detail` 修正后再提交 |
| `401` | 上传会话 token 缺失或无效；这是单次上传会话的能力令牌，不是用户登录或 API Key |
| `403` | 浏览器 Origin 未列入白名单，或服务当前为只读预览 |
| `404` | 项目/资产/变体/素材/候选或文件 ID 不存在 |
| `409` | 版本、排序 revision、上传偏移或幂等键冲突；重新读取当前对象/上传状态再决定是否重试 |
| `413` | 当前请求或简单 multipart 超出限制；大文件改用分块会话 |
| `422` | Pydantic 字段或业务规则校验失败 |
| `507` | 存储空间不足 |

更新请求不要自动用旧版本重试。409 中若有 `current`，把它展示给用户或重新加载，再由用户/业务逻辑决定需要合并的字段。相同上传或批量写入重试时复用同一幂等键。

## API 兼容规则

`/api/v1` 的不破坏性新增（可选字段、新端点）会继续留在 v1。删除/改名字段、修改已有字段语义、改变冲突/幂等行为等破坏性调整需要新版本路径（例如 `/api/v2`），并在迁移窗口内继续服务旧版本。没有单独发布迁移方案前，不移除旧 `/api` 和 `/files` 兼容路径。

## TypeScript Client

仓库提供一个不依赖框架的类型化示例：[team-assets-api-client.ts](../examples/typescript-client.ts)。它覆盖 JSON 错误/409 处理、项目/资产读取、资产更新、图片上传、续传和 SSE 订阅，可直接复制到 React、Vue 或其他前端工程。
