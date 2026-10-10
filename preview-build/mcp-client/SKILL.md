---
name: team-assets-mcp
description: Use the Team Assets shared MCP service to find projects and assets, upload generated images or videos, register candidate-library files, and commit metadata with stable IDs and retry-safe workflows.
metadata:
  short-description: Team Assets MCP workflows
---

# Team Assets MCP

Use this skill when an AI agent needs to inspect or update the shared Team Assets repository.
The MCP server is the single source of truth; this skill defines safe, idempotent interaction standards.

## 1. Connection Boundary

- **Default Endpoint**: Trusted LAN, no-auth endpoint at `http://192.168.10.28:8093/mcp`.
- **Authentication**: No token required on LAN by default. If a deployment enforces Bearer auth, supply it via environment or connector parameters—never hardcode or expose credentials in prompts.
- **Client Helper**: If the calling AI environment cannot speak MCP over HTTP directly, use `scripts/team_assets_mcp.py` as a command-line transport proxy.
- **Skill Version & Auto-Update**: Inspect `get_capabilities` or `get_skill_manifest`. When local files can be modified, run `python scripts/team_assets_mcp.py update` to download, SHA-256 verify, and replace this client package from a validated staging directory.

## 2. Standard Decision Workflow

Follow this strict sequence before executing any mutations:

```
[1. Check Capabilities] ──> [2. Identify Project] ──> [3. Locate Target & Version]
                                                               │
                                                               v
[6. Verify & SSE Event] <── [5. Execute Idempotently] <── [4. User Confirmation]
```

1. **Check Capabilities**: Call `get_capabilities` to confirm server version, max chunk size, and supported features.
2. **Identify Project**: Call `list_projects` to obtain the stable `project_id`. Never guess or hardcode names.
3. **Locate Target & Version**: Call `search_assets`, `get_asset`, or `get_variant` to retrieve the entity's UUID and current `version`.
4. **User Confirmation**: For non-trivial writes (creating, deleting, or overwriting finals), explain the proposed changes (project, asset, variant, episodes) before modifying shared team data.
5. **Execute Idempotently**: Supply a unique, deterministic `idempotency_key` for uploads and commits.
6. **Verify State**: Confirm updated revision or query entity to ensure consistency.

## 3. Core Domain Rules

- **Candidates vs. Asset Materials**: Candidate library records (`candidates`) are project-scoped staging files. Uploading or registering a candidate does NOT automatically bind it to an asset. Use `folder_path` to place a registered candidate in a directory; it is always interpreted as a directory, even when its last segment matches the filename. Use `create_candidate_reference` to associate candidate records with an asset variant.
- **Episode Standard (EPISODES)**: Canonical syntax: empty means unknown; examples: `all`, `3`, `3-10`, `3,8,12-15`, `>35`, `>=35`, `<10`, `<=10`. Do not mix unstructured notes into the `episodes` field.
- **Final Material Selection**: An asset upload is a candidate material by default. Only invoke `set_final_material` when explicitly designating the approved version for production.
- **Optimistic Concurrency**: Update calls require `version`. If concurrent edits occur, the server returns 409 `VERSION_CONFLICT` with the current record.

## 4. Progressive Disclosure (Reference Index)

When detailed specs, parameters, or concrete payload examples are needed, consult the corresponding reference document:

- 📋 **[Tools Inventory](references/tools_inventory.md)**: Current catalog of the 47 MCP tools, verified against the server's `tools/list` response.
- 🚀 **[Workflows & CLI Recipes](references/workflows.md)**: End-to-end recipes for candidate uploads, asset binding, 4K/large file chunked streaming, and bulk project manifest imports.
- 🛡️ **[Error Recovery & Concurrency Guide](references/error_recovery.md)**: In-depth guide to handling 409 conflicts (version, idempotency, reference), offset auto-recovery, and client rollback protection.
