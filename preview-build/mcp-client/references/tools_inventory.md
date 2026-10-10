# Team Assets MCP 工具清单

本清单以服务端 `tools/list` 为准，目前共 **47 个工具**。只读工具标记 `readOnlyHint=true`；写入工具标记 `readOnlyHint=false`。参数名和选项按 MCP schema 记录；未列出的输出字段以工具实际返回为准。

## 1. 工作区与项目（6）

| 工具 | 类型 | 用途 | 参数 |
| :--- | :--- | :--- | :--- |
| `get_workspace` | 只读 | 读取工作区项目、资产、变体和素材快照 | 无 |
| `list_projects` | 只读 | 分页列出项目及稳定 ID | `limit=50`, `offset=0` |
| `create_project` | 写入 | 创建项目 | `name`；可选 `description` |
| `update_project` | 写入 | 修改项目名称或说明，需使用最新版本 | `project_id`, `version`；可选 `name`, `description` |
| `delete_project` | 写入 | 将项目及活跃子项移入回收站 | `project_id`, `version` |
| `restore_project` | 写入 | 恢复项目及本次删除级联移入回收站的子项 | `project_id` |

## 2. 资产与变体（12）

| 工具 | 类型 | 用途 | 参数 |
| :--- | :--- | :--- | :--- |
| `search_assets` | 只读 | 按名称、项目和分类搜索；同名时不要默认取第一项 | 可选 `query=""`, `project_id`, `category`; `limit=50`, `offset=0` |
| `get_asset` | 只读 | 读取资产和其变体，获取当前版本 | `asset_id` |
| `create_asset` | 写入 | 在项目下创建资产 | `project_id`, `name`；可选 `category="人物"`, `role=""` |
| `update_asset` | 写入 | 修改资产名称、分类或定位/用途，需最新版本 | `asset_id`, `version`；可选 `name`, `category`, `role` |
| `delete_asset` | 写入 | 将资产及活跃子项移入回收站 | `asset_id`, `version` |
| `restore_asset` | 写入 | 恢复资产及本次删除级联移入回收站的子项 | `asset_id` |
| `get_variant` | 只读 | 读取变体详情和当前版本 | `variant_id` |
| `create_variant` | 写入 | 在资产下创建造型或状态变体 | `asset_id`, `name`；可选 `episodes=""`, `description=""`, `episode_usages` |
| `update_variant` | 写入 | 修改变体文字、集数或逐集用途；不设置定稿素材 | `variant_id`, `version`；可选 `name`, `episodes`, `description`, `episode_usages` |
| `delete_variant` | 写入 | 将变体移入回收站，不删除资产共享素材 | `variant_id`, `version` |
| `restore_variant` | 写入 | 恢复变体 | `variant_id` |
| `clear_final_material` | 写入 | 清除定稿引用，保留所有候选素材 | `variant_id`, `version` |

`episodes` 使用规范字符串：空字符串代表未知，也支持 `all`、`3`、`3-10`、`3,8,12-15`、`>35`、`>=35`、`<10`、`<=10`。自然语言说明放入 `description`。`episode_usages` 用于结构化记录逐集用途。

## 3. 资产素材与定稿（6）

| 工具 | 类型 | 用途 | 参数 |
| :--- | :--- | :--- | :--- |
| `list_materials` | 只读 | 分页列出资产共享素材；传 `variant_id` 时仍返回其资产的全部素材 | `limit=50`, `offset=0`；`asset_id` 与 `variant_id` 恰选一个 |
| `upload_materials` | 写入 | Base64 小文件兼容上传；大文件使用分块上传和 `commit_intake` | `files`, `idempotency_key`；`asset_id` 与 `variant_id` 恰选一个 |
| `set_final_material` | 写入 | 将同资产的素材设为指定变体定稿 | `variant_id`, `material_id`, `version` |
| `delete_material` | 写入 | 将素材移入回收站并清除引用它的定稿 | `material_id` |
| `restore_material` | 写入 | 恢复已删除素材 | `material_id` |
| `get_material_preview` | 只读 | 获取素材预览内容，供视觉模型查看 | `material_id` |

`upload_materials` 单次最多 20 个文件、合计最多 30 MiB。大文件上传没有固定文件大小上限，使用 `begin_upload` → HTTP 分块传输 → `complete_upload`，随后根据用途调用 `commit_intake` 或 `register_candidate`。上传不会自动设定稿。

## 4. 候选库与目录（10）

| 工具 | 类型 | 用途 | 参数 |
| :--- | :--- | :--- | :--- |
| `list_candidates` | 只读 | 分页列出项目候选素材；搜索只匹配文件名 | `project_id`；可选 `query=""`, `category`, `status="active"`（`active`/`disabled`/`all`）；`limit=50`, `offset=0` |
| `list_candidate_folders` | 只读 | 获取候选库目录树 | `project_id`；可选 `status="all"`（`active`/`disabled`/`all`） |
| `get_candidate` | 只读 | 读取候选素材详情、禁用状态和引用 | `candidate_id` |
| `register_candidate` | 写入 | 将 `complete_upload` 返回的文件登记为候选素材；目标目录使用无歧义的 `folder_path` | `project_id`, `file_id`, `filename`；可选 `category="其它"`, `folder_path`, `relative_path`（旧版完整路径） |
| `create_candidate_reference` | 写入 | 以引用方式把候选素材关联到同项目资产，可选变体 | `candidate_id`, `asset_id`；可选 `variant_id`, `match_status`, `confidence`, `note`, `source` |
| `update_candidate` | 写入 | 修改候选文件名、分类或启用状态 | `candidate_id`, `version`；可选 `name`, `category`, `status`（`active`/`disabled`） |
| `update_candidate_folder` | 写入 | 重命名或禁用目录；禁用状态继承到目录内容 | `folder_id`, `version`；可选 `name`, `status`（`active`/`disabled`） |
| `move_candidate` | 写入 | 将候选文件移动到已有目录或根目录；不会新建目录 | `candidate_id`, `version`, `target_folder_path=""` |
| `reorder_candidate_entries` | 写入 | 设置同一父目录下文件和文件夹的完整混合顺序 | `project_id`, `parent_path`, `entries` |
| `highlight_candidate` | 写入 | 让候选素材对所有访问者临时高亮 60 秒 | `candidate_id` |

`folder_path` 始终表示目标目录，即使目录末级名称与文件名相同也不会被当成文件名剥除。旧 `relative_path` 参数为兼容保留；当它的末级与文件名相同时会按完整文件路径处理。新调用请使用 `folder_path`。当前服务端没有独立的候选文件删除、候选目录新建/删除/恢复工具；不要调用未出现在本清单中的工具名。

## 5. 导入与覆盖审计（4）

| 工具 | 类型 | 用途 | 参数 |
| :--- | :--- | :--- | :--- |
| `plan_project_import` | 只读 | 校验批量导入清单，不写入数据 | `project_id`, `assets`；可选 `on_name_conflict="error"`（`error`/`reuse`） |
| `apply_project_import` | 写入 | 原子应用已校验的批量项目清单，支持幂等重试 | `project_id`, `assets`, `idempotency_key`；可选 `on_name_conflict="error"`（`error`/`reuse`） |
| `audit_project_asset_coverage` | 只读 | 汇总项目资产/变体的素材、定稿和缺口 | `project_id`；可选 `include_disabled=false` |
| `commit_intake` | 写入 | 原子地将已上传文件挂接到资产和变体，或创建资产/变体 | `project_id`, `variants`, `idempotency_key`；`asset_id`（已有资产）与 `asset`（新资产对象）恰选一个 |

`commit_intake.variants` 中每项使用已有 `variant_id`，或通过 `name` 创建新变体；`file_ids` 必须来自 `complete_upload`。`asset` 新建对象支持 `name`，可选 `category="人物"`、`role=""`。一次最多 100 个变体，整批成功或回滚，不会自动设定稿。

## 6. 上传、能力与维护（9）

| 工具 | 类型 | 用途 | 参数 |
| :--- | :--- | :--- | :--- |
| `begin_upload` | 写入 | 创建可断点续传的大文件上传会话 | `filename`, `size`；可选 `sha256`, `idempotency_key` |
| `get_upload_status` | 只读 | 查询上传会话已接收偏移量 | `upload_id` |
| `complete_upload` | 写入 | 校验完整上传并生成可登记的 `file_id` | `upload_id` |
| `cancel_upload` | 写入 | 取消未完成上传并清理暂存分块 | `upload_id` |
| `get_capabilities` | 只读 | 查询协议、上传能力、集数格式和服务状态 | 无 |
| `get_skill_manifest` | 只读 | 查询可移植 Skill/客户端包版本、SHA-256 和下载地址 | 无 |
| `list_trash` | 只读 | 查看回收站中的项目、资产、变体和素材 | 无 |
| `list_history` | 只读 | 查看最近资产变更记录 | 无 |
| `preview_deletion` | 只读 | 预览项目、资产或变体删除影响范围 | `entity_type`（`project`/`asset`/`variant`）, `item_id`, `version` |

分块上传没有服务端固定文件大小上限。每块上限以 `get_capabilities` 返回的 `chunk_size` 为准；网络失败后先查 `get_upload_status`，从服务端返回的偏移量继续。
