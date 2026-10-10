# Team Assets MCP 调用包

这个目录是给其它 AI 客户端、自动化脚本和人工排障使用的可移植调用包。它把
Team Assets 的调用约定、上传顺序和一个不依赖第三方库的 Python CLI 放在一起。
包里包含当前 NAS 的默认局域网地址，但不包含密码或服务 token；地址可以用环境变量覆盖。

## 它和 Skill、MCP 的关系

这三层职责不同：

1. **MCP 服务**是 NAS 上真正执行查询、上传和修改的接口（当前为
   `http://192.168.10.28:8093/mcp`）。
2. **Skill.md** 是给 AI 读的操作规约，告诉它先确认项目和稳定 ID、如何填写集数、何时使用
   分块上传、如何用幂等键重试。它不会启动 MCP，也不会提供权限。
3. **`scripts/team_assets_mcp.py`** 是可选的传输工具，替 AI 客户端处理 JSON-RPC、
   Streamable HTTP、分块上传、断点续传和重复请求。只支持 Python 标准库。

因此，把这个目录复制给其它 AI 是一种可行的 Skill/工具包分发方式；若某个 AI 平台有自己的
Skill 目录，可以复制 `SKILL.md` 和 `references/`，再把脚本注册为工具。若平台原生支持 MCP，
优先直接配置 MCP 连接，脚本只用于不能原生上传本地文件的客户端。

## 连接方式

当前 NAS 是可信局域网内的无认证模式。把这个目录复制给同一局域网中的其它 AI
客户端后，可以直接运行命令；脚本默认连接 `http://192.168.10.28:8093/mcp`：

```powershell
python scripts/team_assets_mcp.py capabilities
```

如果服务地址改变，可设置 `TEAM_ASSETS_MCP_URL` 或传入 `--url`。脚本也兼容可选的
Bearer 模式：传入 `--token` 或设置 `TEAM_ASSETS_MCP_TOKEN` 即可。服务端的 `.env`
不需要复制给 AI，也不需要放进这个目录。

无认证只适用于可信内网：拿到地址的设备可以读写团队数据。分块上传仍会使用每次会话
单独生成的短期 `upload_token`，它只是防止上传块串到别的会话，不是服务登录密钥。

## 查询和更新 Skill

服务提供只读工具 `get_skill_manifest`，返回最新版的版本号、文件清单、下载地址和 SHA-256。
有本地文件权限的 AI 客户端可以运行：

```powershell
python scripts/team_assets_mcp.py update
```

命令会先比较 `VERSION`，再下载压缩包并校验 SHA-256，最后更新当前 `mcp-client` 目录；
更新后需要重新加载 `SKILL.md`。托管式 AI 可以调用 `get_skill_manifest` 读取新 Skill，
但 MCP 不能改写它的系统提示或平台级 Skill。

## CLI

```powershell
# 查看服务版本、上传协议和剩余空间
python scripts/team_assets_mcp.py capabilities

# 查看工具定义
python scripts/team_assets_mcp.py tools

# 调用只读工具
python scripts/team_assets_mcp.py call list_projects --args '{"limit":20}'
python scripts/team_assets_mcp.py call search_assets --args '{"project_id":"PROJECT_ID","query":"洛森"}'

# 大文件上传为候选库素材；图片、视频都走同一套分块协议
python scripts/team_assets_mcp.py upload .\render-4k.png `
  --mode candidate --project-id PROJECT_ID --category 人物 `
  --folder-path "第一集/beat 1"

# 大文件上传后挂到已有变体。已有变体只传 --variant-id，不会覆盖变体文字。
python scripts/team_assets_mcp.py upload .\render-4k.png `
  --mode asset --project-id PROJECT_ID --asset-id ASSET_ID --variant-id VARIANT_ID

# 上传并创建新资产和新变体
python scripts/team_assets_mcp.py upload .\render-4k.png `
  --mode asset --project-id PROJECT_ID --asset-name "阿斯特莱亚" `
  --category 人物 --role 女主角 --variant-name "战衣" --episodes "3-10"
```

`upload` 会先计算 SHA-256，再调用 `begin_upload`，使用会话专属的短期
`upload_token` 逐块 PUT，最后调用 `complete_upload`。候选模式随后调用
`register_candidate`；资产模式随后调用带事务和幂等键的 `commit_intake`。

脚本默认会对读取工具、网络 429/5xx 和带 `idempotency_key` 的写操作做有限指数退避。
没有幂等键的创建、删除、重命名等写操作不会自动重试，以免产生重复或误操作。

## 推荐给 AI 的调用顺序

详细规约在 [SKILL.md](SKILL.md)，可直接提供给支持 Skill 的 AI。核心顺序如下：

1. `get_capabilities`，确认版本和当前上传方式；再 `list_projects`，按名称消歧并保存稳定
   `project_id`。
2. `search_assets` 或 `get_asset`，确认目标资产和最新 `version`；同名时询问用户，不猜第一项。
3. 生图完成后先向用户展示文件、目标项目/资产/变体、集数和说明，用户确认后再写入。
4. 小图且总量不超过 30 MB 时可以使用 `upload_materials` 的 Base64 兼容接口；4K、视频或
   批量文件一律使用 `begin_upload` → HTTP PUT → `complete_upload`。
5. 资产素材用 `commit_intake` 原子入库；候选库文件用 `register_candidate`，需要挂接资产时
   再调用 `create_candidate_reference`。候选文件和资产共享素材是两条不同关系，不要混用。
6. 网络失败先用 `get_upload_status` 读取服务器 `offset`，从该 offset 继续；同一次入库重试
   必须沿用相同的幂等键，内容变化必须换新键。
7. 只有用户明确要求时才调用 `set_final_material`；删除前可用 `preview_deletion` 查看影响。

如果是从 Markdown/表格整理出多个资产，先把同一批数据传给 `plan_project_import`，处理返回的
`errors` 和重复目标，再用相同清单调用 `apply_project_import`。该导入支持 `candidate_ids`、
`candidate_metadata`、`file_ids` 和后端 `episode_usages`，一个事务覆盖整批资产。导入前后可调用
`audit_project_asset_coverage`，生成“已定稿 / 有素材未定稿 / 无素材 / 禁用造成缺口”的机器可读报告。

## 当前边界

- MCP 不接受 NAS 本地路径；AI 必须能读取文件字节，或使用所在平台的文件导出能力。
- `begin_upload` 的原文件大小默认无固定上限（以服务端磁盘和配置为准），单个 HTTP 块最多
  8 MiB。旧 `upload_materials` Base64 通道仍是 30 MB、20 个文件。
- 一个 `commit_intake` 是一个资产的一次原子入库，最多 100 个变体条目；跨多个资产时优先使用
  `plan_project_import` → `apply_project_import`，并为整批生成稳定幂等键。
- `register_candidate` 每次代表一条候选记录；物理文件可能按 SHA-256 复用，但业务记录允许重复。
- 服务器的能力返回值优先于本文件。其它 AI 应先读 `get_capabilities`，只调用已报告的功能。

## 作为库导入

脚本没有安装步骤，可直接加入 Python 工程：

```python
import sys
sys.path.insert(0, "mcp-client/scripts")
from team_assets_mcp import TeamAssetsMCP

client = TeamAssetsMCP()  # 默认连接 NAS；可用 TEAM_ASSETS_MCP_URL 覆盖
projects = client.call_tool("list_projects", {"limit": 20})
file_info = client.upload_file("render.png", idempotency_key="render-job-001")
```

调用方仍需自行调用 `commit_intake` 或 `register_candidate`，这样“上传完成”和“写入哪一类
数据”不会被脚本擅自混为一件事。
