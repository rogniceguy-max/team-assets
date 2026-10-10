# Team Assets MCP 工作流参考

这份参考只在需要上传、重试或批量导入时阅读。工具名称和参数以同一次
`get_capabilities`、`tools/list` 返回为准。

## Streamable HTTP 基础

当前 NAS 无认证时只需要下面三个头；如果连接的是启用 Bearer 的部署，再额外加入
`Authorization`：

```http
Accept: application/json, text/event-stream
Content-Type: application/json
MCP-Protocol-Version: 2025-11-25
```

```http
Authorization: Bearer <MCP连接密钥>  # 仅 bearer 部署需要
```

JSON-RPC 请求示例：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "tools/call",
  "params": {
    "name": "list_projects",
    "arguments": {"limit": 20, "offset": 0}
  }
}
```

支持 MCP 初始化的客户端应先调用 `initialize`，随后发送
`notifications/initialized`；服务端可能返回 `Mcp-Session-Id`，后续请求要原样带上。
当前 NAS 服务是 stateless HTTP，直接工具调用也能工作，但兼容客户端仍应按协议初始化。

## 大文件上传

1. 计算原文件字节数和 SHA-256，生成一个稳定的请求键，例如
   `image-job-20260922-001`。
2. 调用：

```json
{
  "name": "begin_upload",
  "arguments": {
    "filename": "character-4k.png",
    "size": 12345678,
    "sha256": "<64位小写十六进制>",
    "idempotency_key": "image-job-20260922-001"
  }
}
```

3. 读取 `id`、`relative_path`/`upload_url`、`upload_token`、`offset`、`chunk_size`。
   如果 `status` 已是 `ready`，直接使用返回的 `file_id`。
4. 从返回的 `offset` 开始用原始二进制 PUT：

```http
PUT /uploads/<upload-id>?offset=<当前offset>
Authorization: Bearer <upload_token>
Content-Type: application/octet-stream

<最多 chunk_size 字节>
```

每个响应的 `offset` 都是下一次发送位置。请求超时后可重试当前分块或调用
`get_upload_status(upload_id)` 获取当前进度；服务器支持同块幂等重试，遇到 409 offset 冲突时会自动按最新 offset 同步推进。
如果短期上传 token 失效，用同一个 `begin_upload` 幂等键重新调用以取得最新 token。

5. 全部字节成功后调用 `complete_upload(upload_id)`，获得 `file_id`。文件此时只完成了
   文件入库，还没有自动归入资产或候选库。

脚本封装了这段流程：

```powershell
python scripts/team_assets_mcp.py upload .\video.mp4 `
  --mode candidate --project-id PROJECT_ID --folder-path "第二集/beat 3"
```
`--folder-path` 始终表示目标目录；旧别名 `--relative-path` 也按目录解释。目录末级名称即使与文件名相同，也会原样保留。若必须传完整相对文件路径，使用 `--file-relative-path "第二集/beat 3/video.mp4"`。

## 归入资产

已有资产、已有变体只追加素材：

```json
{
  "name": "commit_intake",
  "arguments": {
    "project_id": "PROJECT_ID",
    "asset_id": "ASSET_ID",
    "variants": [{
      "variant_id": "VARIANT_ID",
      "file_ids": ["FILE_ID"],
      "material_names": {"FILE_ID": "character-4k.png"}
    }],
    "idempotency_key": "image-job-20260922-001:intake"
  }
}
```

新资产和新变体用 `asset` 与变体 `name`，而不是同时传 `asset_id`。一次调用只处理一个
资产，最多 100 个变体条目；整个调用成功或回滚。已有变体条目不要混入 name、episodes
或 description，修改文字用 `update_variant` 并提供最新 version。

## 归入候选库

完成上传后：

```json
{
  "name": "register_candidate",
  "arguments": {
    "project_id": "PROJECT_ID",
    "file_id": "FILE_ID",
    "filename": "video.mp4",
    "category": "场景",
    "folder_path": "第一集/beat 1"
  }
}
```
`folder_path` 明确表示目标目录（如 `"第一集/beat 1"`）；目录名与文件名相同也没有歧义。旧参数 `relative_path` 为兼容旧调用保留。要传含文件名的完整相对路径，新 CLI 使用 `--file-relative-path`，MCP 调用可继续用 `relative_path`；不要同时传 `folder_path` 和 `relative_path`。

如需让资产使用这条候选记录，再调用：

```json
{
  "name": "create_candidate_reference",
  "arguments": {
    "candidate_id": "CANDIDATE_ID",
    "asset_id": "ASSET_ID",
    "variant_id": "VARIANT_ID",
    "match_status": "approximate",
    "confidence": 0.72,
    "note": "同文件夹与角色名推断",
    "source": "import"
  }
}
```

同一物理文件可以因为 SHA-256 去重而复用，但候选记录本身允许重复。不要因为文件已存在
就擅自跳过用户要求的候选记录；先读取并确认目标关系。

## 重试表

| 情况 | 做法 |
|---|---|
| HTTP 408/429/5xx | 有界指数退避；读操作可重试，写操作仅在有幂等键时重试 |
| 分块 PUT 超时 | `get_upload_status`，从服务器 offset 继续 |
| 分块 409 offset 冲突 | 使用错误中的 offset；不要猜测本地进度 |
| 401/403 上传 token | 用同一 begin 幂等键重新取短期上传 token |
| 409 版本冲突 | 读取 current，合并后使用最新 version |
| `retryable=false` 校验错误 | 修改参数后再请求，不要重复发送 |
| 同幂等键不同内容 | 停止并换一个新键；不要覆盖原请求 |

## 生成项目导入

若服务的能力列表已提供 `plan_project_import`、`apply_project_import` 或
`audit_project_asset_coverage`，先使用 plan/audit 类只读工具检查重复、未知集数、缺少候选
和目标歧义，再执行 apply。当前客户端的 `call` 命令可以调用新工具，不需要升级脚本：

```powershell
python scripts/team_assets_mcp.py call plan_project_import --args-file .\manifest.json
```

manifest 中应使用稳定 ID；名称只用于展示或在明确无歧义时创建新对象。导入批次要有自己的
幂等键，避免 AI 在上下文重试时重复创建资产。
