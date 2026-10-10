# 集数与资产筛选（2026-09-11）

## 写入约定

MCP 创建、更新、批量入库以及 HTTP API 均使用同一个 `episodes` 校验器。输入字符串只接受正整数、逗号分隔区间、比较符、`all` 或空字符串。自然语言说明放到 `description`。

| 输入 | 规范值 | 结构化含义 |
| --- | --- | --- |
| 空字符串 | 空字符串 | unknown |
| all | all | 全程 |
| 3,8,12-15 | 3,8,12-15 | 三个区间，含端点 |
| 3-8,5-10 | 3-10 | 合并重叠和相邻区间 |
| >35 | >=36 | 36 起，没有上限 |
| >=35 | >=35 | 35 起，没有上限 |
| <10 | 1-9 | 不包含 10 |
| <=10 | 1-10 | 包含 10 |

非法输入返回 422 / INVALID_EPISODES，不写入数据库。未提供更新字段表示保留原值；更新为 `""` 表示明确改为未知。

所有变体读取接口返回 `episode_scope` 对象，例如：

```json
{"kind":"ranges","ranges":[{"start":3,"end":10},{"start":36,"end":null}]}
```

`kind` 为 `all`、`unknown` 或 `ranges`；数据库以 JSON TEXT 保存，API 解码为对象。MCP `get_capabilities` 返回版本 2.3 和 `structured_episodes` 能力。连接中的 AI 应刷新工具列表，获取新版字段说明。

## 后端出场方式

`episode_usages` 是只供 MCP 和审计使用的结构化补充字段，不改变前端已有的 `episodes` 时间线筛选。每项格式如下：

```json
{"episodes":"3-5","mode":"voice","note":"门外声音"}
```

`episodes` 使用相同的规范语法；`mode` 建议使用 `on_screen`、`mentioned`、`voice`、`off_screen`、`flashback`、`costume_change` 或 `other`，`note` 用于简短补充。服务会为每项附加 `episode_scope`，未知集数不能写入出场方式记录。

## 旧数据

启动迁移在事务内幂等执行，增加变体版本与一次 workspace revision。能确定的“第1—5、10章”等转换为规范值。无法确定的保留原 `episodes`，结构标记 unknown，`episodes_needs_review=true`。网页以“未知”展示，原文仍可搜索和在编辑时查看。

`episodes_original` 保留首次迁移原值；`episode_migration_report` 保留转换审计记录。此后主动修改 episodes 会清除待确认标志并生成新结构。仅编辑名称或描述不改变未知集数；旧客户端原样提交未确认原文也不会被阻断。

## 8094 筛选规则

分类互斥；时间线和搜索分别在资产内任一变体上命中即可，可以命中不同变体。资产名称和定位也参与搜索。命中后展示资产全部变体，包括未知集数和区间之外的变体。

“仅显示尚未定稿”默认关闭。开启后仅展示无 final_material_id 的变体，没有可展示变体的资产完全隐藏。它不会改变之前资产是否命中搜索/时间线的判断。

前端仅比较 episode_scope 数值范围，不再解析自由文字。未知集数本身不命中时间线；可随同一资产其他变体的命中结果展示。

Dock 使用最新版 Figma 426×50、9 个按钮。页面根节点预留稳定滚动条宽度。原 8092 前端未替换，8094 仍连接共享后端。
