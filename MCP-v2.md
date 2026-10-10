> 2026-09-22：MCP 能力版本 2.4。在 2.3 的基础上新增 NAS Skill 版本清单、SHA-256 校验和客户端包更新；详见 [集数标准化](EPISODES.md) 和 `mcp-client/` 调用包。

# MCP v2 使用说明

本版优先扩展 MCP，前端 HTML、CSS、JavaScript 未修改。服务地址仍为
`http://192.168.10.28:8093/mcp`，当前 NAS 部署是可信局域网内的无认证模式，
其它 AI 只需拿到 `mcp-client/` 即可连接。若部署到不可信网络，可显式设置
`MCP_AUTH_MODE=bearer` 和 `TEAM_ASSETS_MCP_TOKEN` 恢复 Bearer 认证；不要把服务器
`.env` 复制给模型。客户端重新连接并刷新工具列表即可获取新工具。`mcp-client/` 提供
不含真实密钥的 Skill 说明和标准库 CLI。

## 查找和管理

| 用途 | 工具 |
|---|---|
| 查看版本、格式、大小配置和剩余空间 | get_capabilities |
| 分页查项目 | list_projects |
| 按名称、项目和分类查资产 | search_assets |
| 查看指定资产、变体 | get_asset / get_variant |
| 分页查素材及尺寸、校验值 | list_materials |
| 向视觉模型返回素材缩略图 | get_material_preview |
| 创建项目、资产、变体 | create_project / create_asset / create_variant |
| 修改项目信息、资产信息、变体信息 | update_project / update_asset / update_variant |
| 预览删除范围 | preview_deletion |
| 删除项目、资产、变体 | delete_project / delete_asset / delete_variant |
| 恢复项目、资产、变体 | restore_project / restore_asset / restore_variant |
| 删除、恢复单张素材 | delete_material / restore_material |
| 设置、取消定稿 | set_final_material / clear_final_material |
| 回收站、操作记录 | list_trash / list_history |
| 项目素材覆盖和缺口 | audit_project_asset_coverage |
| 查询最新版 Skill/客户端包 | get_skill_manifest |
| 多资产清单预检、原子导入 | plan_project_import / apply_project_import |

保留旧 get_workspace 返回全量内容以兼容已有客户端。资产多时优先使用分页搜索，避免给 AI 返回整个项目库。路径和稳定 ID 用于同名消歧；版本号用于避免覆盖其他端修改。

删除会级联标记该次仍有效的子项；恢复只恢复同一删除批次，不恢复之前单独删除的条目。恢复子项前，父项必须有效。父项删除期间保留其定稿指向，以便同批恢复；单张定稿素材删除会取消定稿。删除不立即清除原文件。删除最后项目后重启也不会重新创建默认项目。

用户明确授权后无需重复询问。目标或字段不明确时先澄清。工具描述不等于服务器能验证对话中的确认；授权判断仍由 AI 客户端负责。

## 大图片上传

新会话通道默认 **不设置原文件字节大小上限**，配置 `MAX_FILE_BYTES=0` 表示不设上限。每个 HTTP 块最多 8 MiB，这是分块大小，不是文件大小。剩余磁盘空间、图片有效性及异常像素尺寸保护仍生效，原图不会压缩。支持 PNG、JPEG、WebP、GIF。

1. 调用 `begin_upload(filename, size, sha256, idempotency_key)`。size 是原文件字节数；建议提供 SHA-256。
2. 从返回值读取 `id`、`upload_url`、`upload_token`、`offset` 和 `chunk_size`。
3. 使用有文件读取能力的 HTTP 客户端，从 offset 开始逐块发送原始二进制：

```http
PUT <upload_url>?offset=0
Authorization: Bearer <upload_token>
Content-Type: application/octet-stream

<最多 chunk_size 字节的原文件内容>
```

4. 每次以服务器返回的 offset 为准继续。网络中断时调用 `get_upload_status(upload_id)`；同位置同内容重试不会重复追加，内容不一致返回冲突。
5. 全部发送完毕，调用 `complete_upload(upload_id)` 校验完整性并生成缩略图，获得 `file_id`。
6. 调用 `commit_intake` 将文件加入目标资产/变体。

上传 token 只授权该上传会话，不可用作 MCP 密钥。会话默认24小时到期；未完成会话可 `cancel_upload` 清理暂存文件。重复 begin 相同请求标识会返回相同会话并轮换 token，应使用最新 token。过期后以新请求标识创建新会话。完成后取消不会删除已登记文件。

NAS 使用 `TEAM_ASSETS_PUBLIC_MCP_URL=http://192.168.10.28:8093` 返回客户端可访问的上传地址；反向代理时填写对应外部地址。无认证模式不需要 `TEAM_ASSETS_MCP_TOKEN`。

AI 必须能获取图片文件并调用 HTTP，单有 MCP 工具连接不会使它自动获得本地文件访问能力。无需将巨大 Base64 文本交给模型。旧 `upload_materials` Base64 兼容通道仍限制30MB/20张；网页本版也仍保留原限制。大图请使用新上传会话。

## 一次完成元数据入库

已有资产与已有变体追加图片：

```json
{
  "project_id": "目标项目ID",
  "asset_id": "目标资产ID",
  "variants": [{
    "variant_id": "目标变体ID",
    "file_ids": ["complete_upload返回的file_id"],
    "material_names": {"complete_upload返回的file_id": "战衣4K.png"}
  }],
  "idempotency_key": "这一次入库的稳定请求标识"
}
```

新资产与新变体：

```json
{
  "project_id": "目标项目ID",
  "asset": {"name": "阿斯特莱亚", "category": "人物", "role": "女主角"},
  "variants": [{"name": "战衣", "episodes": "第1集", "description": "金色盔甲", "file_ids": ["文件ID"]}],
  "idempotency_key": "另一条稳定请求标识"
}
```

asset 与 asset_id 恰好提供一个；已有变体仅追加文件，不在此工具内覆盖文字。一次最多100个变体条目，每条最多100个文件引用。元数据、素材引用、变更记录和幂等结果同事务提交；任何条目失败，整个入库回滚。已完成上传的文件仍可重新用于修正后的提交。

同键同内容重试返回原结果，同键不同内容返回冲突。幂等键用于重试一次操作，不是对象永久身份。若原结果后来被删除，同键重试返回历史结果，不会重新创建它。需要新操作时使用新键。

## 后端出场方式

`episodes` 继续作为前端时间线的简洁筛选字段。需要记录“正面出场、仅提及、门外声音、回忆、换装”等细节时，在创建或更新变体时传 `episode_usages`：

```json
[{"episodes":"3-5","mode":"voice","note":"门外声音"}]
```

每项会同时保存规范化的 `episode_scope`。这是 MCP/审计字段，当前前端不会展示或改变时间线规则。

## 项目清单导入与覆盖报告

对 Markdown/JSON 整理出的多资产清单，先调用 `plan_project_import`。清单中的变体可以带 `file_ids`（已完成上传的文件）、`candidate_ids`（候选库记录）、`candidate_metadata`（匹配依据）和 `episode_usages`。预检返回重复名称、无效集数、缺失文件和跨项目候选引用，不写入数据库。确认 `valid=true` 后，用同一清单和新的 `idempotency_key` 调用 `apply_project_import`；它在一个事务中创建/复用资产、变体、素材和候选引用。

`audit_project_asset_coverage` 是只读报告，用于区分已定稿、已有素材但未定稿、没有可用素材，以及禁用候选造成的缺口。默认遵循前端正常视图排除禁用候选，后端审计可传 `include_disabled=true`。

报告中资产级 `coverage_status` 表示该资产是否有可用素材；`candidate_coverage_status` 单独表示是否有关联候选记录。直接上传到资产的素材会计入素材覆盖，但不会计为候选库覆盖。

候选引用还可以附带匹配审计信息：`match_status` 可为 `folder`、`web`、`exact`、`approximate`、`partial`、`missing` 或 `unreviewed`；`confidence` 为 0–1，`note` 记录依据，`source` 记录来源。引用记录会在候选查询中返回，不改变候选文件自身的分类和状态。

## 错误处理

业务错误返回 `isError=true` 与 `structuredContent.error`，包含 code、message、status、retryable 等字段。版本冲突附 current 最新内容；父项已删除时附 parent_type/parent_id；分块冲突附当前 offset。冲突应先读取并合并或调整请求，不能无条件自动覆盖。

本版未实现远程 URL 导入、跨项目移动资产或永久清空回收站。前端新按钮与大图交互留待后续。
