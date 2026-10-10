# Team Assets MCP 错误排查与恢复指南 (Error Recovery)

本指南针对调用 MCP 工具时可能遇到的异常、HTTP 状态码及数据冲突提供明确的应对策略。

---

## 1. 结构化错误协议 (Structured Error Protocol)

所有工具调用失败时，均返回保留完整上下文的结构化对象：

```json
{
  "isError": true,
  "structuredContent": {
    "error": {
      "code": "VERSION_CONFLICT",
      "message": "内容已更新，请重新读取",
      "status": 409,
      "retryable": false,
      "current": { ... }
    }
  }
}
```

- **`retryable == true`**：临时性错误（如网络波动、429 限流、502/503 节点繁忙、上传 offset 自动对齐），应当使用带抖动的指数退避重试。
- **`retryable == false`**：业务或语义冲突（如版本过期、参数验证不通过、幂等键冲突、同名冲突），**绝对禁止**盲目重试，必须根据返回的 `current` 调整后重新发业务请求。

---

## 2. 409 状态码核心分类与应对方案

### (1) 版本并发冲突 (`VERSION_CONFLICT`)
- **成因**：多端或多人并发编辑同一对象（如项目、资产、变体、候选等），提交的 `version` 小于数据库当前版本。
- **应对方案**：
  1. 从返回的 `error.current` 读取最新记录；
  2. 合并本地变更意图与远端最新字段；
  3. 使用 `current.version` 再次提交修改。

### (2) 幂等键冲突 (`IDEMPOTENCY_CONFLICT`)
- **成因**：重复使用了相同的 `idempotency_key`，但提交了不同的业务内容（例如换了目标资产 ID 或不同的变体名称）。
- **应对方案**：
  - 如果意图是重试先前的失败请求：必须保持请求 payload、目标 ID 与文件名 100% 相同；
  - 如果意图是提交全新或不同的内容：必须生成全新的 `idempotency_key`。

### (3) 候选重复引用冲突 (`CANDIDATE_ALREADY_REFERENCED`)
- **成因**：在执行 `restore_material` 或 `create_candidate_reference` 时，同一个候选素材已经在该资产中存在未删除的活跃素材记录。
- **应对方案**：
  - 检查返回的冲突 `material_id`，无需重复恢复或绑定；
  - 若需为新变体设置定稿，直接调用 `set_final_material` 指向现存素材即可。

---

## 3. 大文件分块上传断点续传与对齐

在执行 `PUT /uploads/<upload_id>?offset=<offset>` 时：

1. **遇到网络超时或连接中断**：
   - 不要从 0 重新开始上传；
   - 调用 `get_upload_status(upload_id)` 查询服务器当前确认的实际 `offset`；
   - 本地文件 `seek(offset)` 后继续发送后续分块。
2. **遇到 409 Offset Conflict**：
   - 说明并发或重试导致 offset 不匹配；响应体中会返回服务端的最新确认 `offset`；
   - 客户端自动按该 `offset` 同步调整指针并重发当前块（CLI 已内置自动同步）。
3. **遇到 401 / 403 Token 过期**：
   - 上传会话的 `upload_token` 是临时凭证；
   - 使用相同的 `idempotency_key` 再次调用 `begin_upload`，服务端将返回相同会话但签发新的 `upload_token`，随后即可继续上传。

---

## 4. 客户端 Skill 更新与版本回退保护

运行 `python scripts/team_assets_mcp.py update` 时：

1. **版本对比与降级拦截**：
   - 检查远端 `get_skill_manifest` 中的版本；
   - 若本地版本已与远端一致（`already_current`），跳过更新；
   - 若本地版本高于服务端版本（`server_version_lower`），默认拒绝降级，防止误覆盖本地前沿版本；如需强制覆盖需指定 `--force`。
2. **完整性校验与目录替换**：
   - 下载 zip 后严格比对 SHA-256 哈希；
   - 解压至 staging 临时目录，校验必须包含 `SKILL.md`、`VERSION`、`scripts/team_assets_mcp.py`；
   - 在目标目录同级创建并校验完整的新包目录，再将旧目录移到备份路径并安装新目录；
   - 若安装新目录失败，会尝试把旧目录恢复到原路径；恢复失败时，错误会返回保留旧包的备份路径；
   - 成功更新后按新包完整镜像目录，旧版本遗留文件不会继续保留。解压或清单校验失败发生在切换之前，不会改动现有安装。
