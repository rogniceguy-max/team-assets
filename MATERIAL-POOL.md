# 资产候选池

素材的 `asset_id` 是必填、稳定的所属资产。`variant_id` 仅保留旧上传来源，可为空；它不再决定素材能被哪个变体使用。同一素材可同时被同资产多个变体设为定稿，也可分别选用不同素材。跨资产引用和已删除素材引用会被拒绝。

## HTTP 与 MCP

- `POST /api/assets/{asset_id}/materials`：multipart `files`、可选 `source` 和 `Idempotency-Key`，无需预先建立变体。与旧上传接口一样是小文件兼容通道（每批最多20张、30 MB）；已有分块上传通道继续用于大文件。
- `GET /api/assets/{asset_id}/materials?limit=50&offset=0`：分页列出整个资产候选池。
- `POST /api/variants/{variant_id}/materials` 保留，自动填入所属资产与变体来源。
- `/api/state` 的素材增加 `asset_id`、`width`、`height`，供候选区按原比例排版。
- MCP `list_materials`、`upload_materials` 的 `asset_id` 与 `variant_id` 恰选一个；按变体查询也返回所属资产的整个候选池。
- `commit_intake` 保留现有批量变体入库格式，创建素材时自动填入资产归属；不会自动设定稿。
- `set_final_material` 和 HTTP 变体 PATCH 仍需最新 `version`；只修改目标变体。

## 删除与恢复

删除变体不删除共享候选，其他变体的定稿保持不变。删除资产或项目仍连同其活跃候选一起进入回收站，包括没有来源变体或来源变体已删除的素材。

删除单个候选时，在一个事务内清空全部引用它的变体定稿并递增版本，包括已删除变体，避免以后恢复出失效引用。恢复候选不会自动重新设为定稿。恢复资产/项目遵循原有删除批次，不恢复此前单独删除的内容；兼容恢复旧版本随变体一起删除的素材。

## 数据迁移

启动时在事务内把旧 `materials` 表复制为同名新表，解除旧 `variant_id NOT NULL` 约束并从所属变体回填 `asset_id NOT NULL`。保留所有素材 ID、rowid 顺序、文件引用、版本和回收站批次；不复制或改写图片文件。重复启动不会再次迁移已有新表。`asset_id` 外键及候选池索引保证归属与读取效率。

部署前必须备份数据库，web 与 MCP 都需要使用新后端，避免旧 MCP 写入缺少 `asset_id` 的素材。旧前端接口保留，但资产级候选展示由8094新前端负责。
