# 候选库 API

候选记录按项目归属，和资产候选池 `materials` 分开。记录允许重复；相同文件内容在 `files` 表中按 SHA-256 复用原图和缩略图，所以重复上传不会重复占用原始文件空间。

## 浏览

`GET /api/candidates?project_id=<id>&query=<filename>&status=active|disabled|all&category=人物|场景|道具|其它&limit=50&offset=0`

- 默认只返回 `active`，`status=disabled` 对应 Dock 的“仅显示禁用”。
- `query` 只匹配候选记录的 `name`（文件名），不匹配文件夹路径。
- 返回的 `url`、`thumbnail_url`、`mime`、`width`、`height` 可以直接用于图片或视频卡片；视频缩略图为首帧。
- `relative_path` 保留拖入文件夹时的浏览器相对路径，显示名 `name` 始终是 basename。
- `candidate_folders` 保存由上传路径自动生成的目录树；目录本身不是上传对象，不提供新建接口。

目录页把同一父目录下的文件夹、图片和视频放在一套 `sort_order` 中。点击文件或视频打开预览，点击文件夹进入目录；长按约 420ms 后拖动，拖到文件上交换同级顺序，拖到文件夹上把文件移入该目录。搜索时仍按文件名展平显示，拖动只允许在同一父目录内交换，避免产生含义不清的跨目录排序。

## 上传

`POST /api/candidates` 使用 multipart：`project_id`、可选 `category`，一个或多个 `files`。服务端按流复制到 staging，不使用旧素材接口的 30 MiB 限制；图片和常见视频均可上传，文件夹由浏览器传入的相对路径保留。视频首帧由 ffmpeg 生成；没有 ffmpeg 时保留可播放原视频并生成中性缩略图。

大文件或视频也可以沿用分块流程：

1. `begin_upload`；
2. 按返回的 `upload_url`、`upload_token` 分块 PUT；
3. `complete_upload`（现在支持图片和视频并生成首帧）；
4. `POST /api/candidates/from-file`，JSON 为 `{project_id,file_id,filename,folder_path?,relative_path?,category?}`。优先使用 `folder_path` 表示目标目录，即使目录末级名称与文件名相同也没有歧义。旧 `relative_path` 为兼容字段；当末级与文件名相同时，它按完整文件路径处理。不要同时传两个字段。

MCP 对应 `register_candidate`。

## 操作

- `PATCH /api/candidates/{id}`：携带 `version` 修改 `name`、`category`、`status`（`active`/`disabled`）。禁用只从默认候选列表隐藏，不删除文件或引用。
- `POST /api/candidates/{id}/highlight`：设置 60 秒临时高亮；MCP 也提供同名工具。
- `GET /api/candidate-folders?project_id=<id>&status=active|disabled|all`：读取由上传路径产生的目录树。文件夹状态会向子目录和目录内文件继承。
- `PATCH /api/candidate-folders/{id}`：携带 `version` 修改文件夹 `name` 或 `status`。重命名会同步更新所有后代路径；禁用文件夹会让后代文件在默认列表中隐藏，恢复父文件夹后才会重新出现。
- `POST /api/candidates/{id}/move`，JSON `{version,target_folder_path}`：将候选文件移到已有目录或根目录。
- `POST /api/candidate-entries/reorder`，JSON `{project_id,parent_path,entries:[{type:'folder'|'candidate',id}]}`：以完整同级列表提交混合排序，服务端拒绝过期或缺项列表。
- `POST /api/candidates/{id}/references`，JSON `{asset_id,variant_id?,match_status?,confidence?,note?,source?}`：创建跨候选库到资产的逻辑引用，不复制文件，同时创建资产候选池可见的 `materials` 引用行。同一候选在一个资产中只产生一条 material，可被多个变体继续设为定稿。匹配字段用于记录 AI 推断依据，`confidence` 范围为 0–1。
- `DELETE /api/candidates/{id}/references?asset_id=<id>&variant_id=<id?>`：移除关系；不会物理删除候选文件。

候选自身状态和素材引用状态相互独立。资产或变体进入回收站时，读取候选的 `references` 会暂时过滤对应的已删除父级，恢复后关系仍保留。
