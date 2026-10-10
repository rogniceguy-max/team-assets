"""Validated episode syntax and conservative, one-time legacy conversion."""
import json
import re
from fastapi import HTTPException

EPISODES_HELP = "使用集数 episodes 仅支持空字符串(未知)、all(全程)、3、3-10、3,8,12-15、>35(36起)、>=35(35起)、<10、<=10。集数为正整数；不接受自然语言说明，请放入 description。返回 episode_scope 为结构化范围。"
EPISODE_USAGE_HELP = "可选的后端出场方式记录。每项使用 {episodes, mode, note}：episodes 仍使用规范数字范围，mode 建议使用 on_screen、mentioned、voice、off_screen、flashback、costume_change、other；note 为补充说明。该字段供 MCP/审计使用，前端时间线仍只读取 episodes。"
UNKNOWN = {"kind": "unknown", "ranges": []}


def parse_episodes(value):
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("集数字段应为不超过 2000 字符的字符串")
    value = re.sub(r"\s+", "", value)
    if not value:
        return "", dict(UNKNOWN)
    if value.lower() == "all":
        return "all", {"kind": "all", "ranges": []}
    ranges = []
    for part in value.split(","):
        m = re.fullmatch(r"(>=|>|<=|<)?([1-9][0-9]*)(?:-([1-9][0-9]*))?", part)
        if not m:
            raise ValueError(EPISODES_HELP)
        op, number, end = m.groups()
        number = int(number)
        if op and end:
            raise ValueError("比较符不能与区间混用")
        if op in (">", ">="):
            start, end = number + (op == ">"), None
        elif op in ("<", "<="):
            start, end = 1, number - (op == "<")
        else:
            start, end = number, int(end) if end else number
        if start > 2147483647 or (end is not None and (end < start or end > 2147483647)):
            raise ValueError("集数必须为正整数且起始不得超过结束，最大 2147483647")
        ranges.append({"start": start, "end": end})
    merged = []
    for item in sorted(ranges, key=lambda r: r["start"]):
        if merged and (merged[-1]["end"] is None or item["start"] <= merged[-1]["end"] + 1):
            prev = merged[-1]
            prev["end"] = None if prev["end"] is None or item["end"] is None else max(prev["end"], item["end"])
        else:
            merged.append(item.copy())
    canonical = ",".join(f">={r['start']}" if r["end"] is None else str(r["start"]) if r["start"] == r["end"] else f"{r['start']}-{r['end']}" for r in merged)
    return canonical, {"kind": "ranges", "ranges": merged}


def episode_fields(value):
    try:
        canonical, scope = parse_episodes(value)
    except ValueError as exc:
        raise HTTPException(422, {"code": "INVALID_EPISODES", "message": str(exc)}) from exc
    return {"episodes": canonical, "episode_scope": json.dumps(scope), "episodes_needs_review": 0}


def normalize_episode_usages(value):
    """Validate backend-only appearance records and store structured JSON.

    ``episodes`` remains the compact field used by the existing UI.  These
    records preserve finer-grained information such as mentioned/voice-only
    appearances without changing the front-end filtering contract.
    """
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > 200:
        raise ValueError("episode_usages 应为最多 200 项的列表")
    normalized = []
    for index, item in enumerate(value, 1):
        if not isinstance(item, dict) or set(item) - {"episodes", "mode", "note"}:
            raise ValueError(f"episode_usages 第 {index} 项字段不正确")
        episodes = item.get("episodes")
        mode = item.get("mode")
        note = item.get("note", "")
        if not isinstance(episodes, str) or not episodes.strip():
            raise ValueError(f"episode_usages 第 {index} 项需要 episodes")
        if not isinstance(mode, str) or not mode.strip() or len(mode.strip()) > 50:
            raise ValueError(f"episode_usages 第 {index} 项需要 1–50 字符的 mode")
        if not isinstance(note, str) or len(note) > 1000:
            raise ValueError(f"episode_usages 第 {index} 项 note 不能超过 1000 字符")
        try:
            canonical, scope = parse_episodes(episodes)
        except ValueError as exc:
            raise ValueError(f"episode_usages 第 {index} 项：{exc}") from exc
        if not canonical:
            raise ValueError(f"episode_usages 第 {index} 项不能使用未知集数")
        normalized.append({
            "episodes": canonical,
            "episode_scope": scope,
            "mode": mode.strip(),
            "note": note.strip(),
        })
    return normalized


def episode_usage_fields(value):
    try:
        normalized = normalize_episode_usages(value)
    except ValueError as exc:
        raise HTTPException(422, {"code": "INVALID_EPISODE_USAGES", "message": str(exc)}) from exc
    return {"episode_usages": json.dumps(normalized, ensure_ascii=False)}


def variant_record(row):
    result = dict(row)
    scope = result.get("episode_scope")
    result["episode_scope"] = json.loads(scope) if isinstance(scope, str) else scope or dict(UNKNOWN)
    result["episodes_needs_review"] = bool(result.get("episodes_needs_review", False))
    usages = result.get("episode_usages")
    if isinstance(usages, str):
        try:
            result["episode_usages"] = json.loads(usages)
        except (TypeError, ValueError):
            result["episode_usages"] = []
    elif not isinstance(usages, list):
        result["episode_usages"] = []
    return result


def legacy_episodes(value):
    try:
        return (*parse_episodes(value), False)
    except ValueError:
        pass
    text = re.sub(r"\s+", "", value)
    if text in {"全程", "全集", "全部集数"}:
        return "all", {"kind": "all", "ranges": []}, False
    if text in {"未知", "待定", "未填写", "不确定"}:
        return "", dict(UNKNOWN), False
    if re.fullmatch(r"前[1-9][0-9]*(集|章)", text):
        canonical, scope = parse_episodes("1-" + re.search(r"[0-9]+", text)[0])
        return canonical, scope, False
    # Only full-string numeric descriptions are safe; annotations are retained for review.
    text = text.translate(str.maketrans({"—": "-", "–": "-", "～": "-", "~": "-", "，": ",", "、": ","}))
    text = text.replace("至", "-").replace("到", "-")
    if re.fullmatch(r"(?:第)?\d+(?:集|章)(?:起|及以后)", text):
        text = ">=" + re.search(r"\d+", text)[0]
    elif re.fullmatch(r"(?:第)?\d+(?:集|章)之后", text):
        text = ">" + re.search(r"\d+", text)[0]
    else:
        text = text.replace("第", "").replace("集", "").replace("章", "")
    try:
        canonical, scope = parse_episodes(text)
        return canonical, scope, False
    except ValueError:
        return value, dict(UNKNOWN), True


def migrate_episodes(c):
    columns = {r["name"] for r in c.execute("PRAGMA table_info(variants)")}
    for name, definition in (("episode_scope", "TEXT"), ("episodes_original", "TEXT"), ("episodes_needs_review", "INTEGER NOT NULL DEFAULT 0"), ("episode_usages", "TEXT NOT NULL DEFAULT '[]'")):
        if name not in columns:
            c.execute(f"ALTER TABLE variants ADD COLUMN {name} {definition}")
    c.execute("CREATE TABLE IF NOT EXISTS episode_migration_report(variant_id TEXT PRIMARY KEY, original TEXT NOT NULL, canonical TEXT NOT NULL, needs_review INTEGER NOT NULL)")
    rows = c.execute("SELECT id,episodes FROM variants WHERE episode_scope IS NULL").fetchall()
    for row in rows:
        canonical, scope, review = legacy_episodes(row["episodes"])
        c.execute("UPDATE variants SET episodes=?,episode_scope=?,episodes_original=?,episodes_needs_review=?,version=version+1 WHERE id=?", (canonical, json.dumps(scope), row["episodes"], int(review), row["id"]))
        c.execute("INSERT OR IGNORE INTO episode_migration_report VALUES(?,?,?,?)", (row["id"], row["episodes"], canonical, int(review)))
    return len(rows)
