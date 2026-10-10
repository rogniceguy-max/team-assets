import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi import HTTPException

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from episodes import parse_episodes, legacy_episodes
from server import AssetService
from intake import IntakeService


@pytest.mark.parametrize("text,canonical,ranges", [
    ("3,8,12-15", "3,8,12-15", [(3,3),(8,8),(12,15)]),
    ("5-10,3-7,11", "3-11", [(3,11)]),
    (">35", ">=36", [(36,None)]),
    (">=35", ">=35", [(35,None)]),
    ("<10", "1-9", [(1,9)]),
    ("<=10", "1-10", [(1,10)]),
])
def test_ranges(text, canonical, ranges):
    actual, scope = parse_episodes(text)
    assert actual == canonical
    assert scope == {"kind":"ranges", "ranges":[{"start":a,"end":b} for a,b in ranges]}


@pytest.mark.parametrize("text", ["0", "10-3", "<1", "第3集", "1.5", "3/10", "all,3", "3,,4", "-3", ">2147483647"])
def test_invalid(text):
    with pytest.raises(ValueError):
        parse_episodes(text)


def test_unknown_and_conservative_legacy():
    assert parse_episodes("")[1]["kind"] == "unknown"
    assert parse_episodes("all")[1]["kind"] == "all"
    assert legacy_episodes("第11—15、21、71—72章")[0] == "11-15,21,71-72"
    assert legacy_episodes("前100章")[0] == "1-100"
    assert legacy_episodes("35集以后")[2] is True
    assert legacy_episodes("第1章前世回忆")[1]["kind"] == "unknown"


def setup_asset(tmp_path):
    service = AssetService(tmp_path)
    project = service.state()["projects"][0]["id"]
    asset = service.create_asset(project, "角色")
    return service, project, asset


def test_all_write_paths_and_read_shapes(tmp_path):
    s,p,a = setup_asset(tmp_path)
    v=s.create_variant(a["id"], "日常", "3-10,8-12")
    assert v["episodes"] == "3-12"
    assert s.state()["variants"][0]["episode_scope"] == v["episode_scope"]
    assert s.get_asset(a["id"])["variants"][0]["episode_scope"] == v["episode_scope"]
    v=s.update_variant(v["id"],v["version"],episodes=">35")
    assert s.get_variant(v["id"])["episode_scope"]["ranges"] == [{"start":36,"end":None}]
    with pytest.raises(HTTPException) as exc:
        s.update_variant(v["id"],v["version"],episodes="第3章")
    assert exc.value.status_code == 422
    intake=IntakeService(s)
    with pytest.raises(HTTPException):
        intake.commit_intake(p,[{"name":"ok","episodes":"all"},{"name":"bad","episodes":"日常"}],"bad",asset_id=a["id"])
    assert len(s.state()["variants"]) == 1
    result=intake.commit_intake(p,[{"name":"new","episodes":"<=10"}],"ok",asset_id=a["id"])
    assert result["variants"][0]["episode_scope"]["ranges"] == [{"start":1,"end":10}]


def test_migration_concurrent_idempotent_and_old_form_roundtrip(tmp_path):
    s,p,a = setup_asset(tmp_path)
    v=s.create_variant(a["id"], "旧状态")
    with s.connect(write=True) as c:
        c.execute("UPDATE variants SET episodes='第1章前世回忆',episode_scope=NULL WHERE id=?", (v["id"],))
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda _:AssetService(tmp_path),range(2)))
    v=s.get_variant(v["id"])
    assert v["version"] == 2
    assert v["episode_scope"]["kind"] == "unknown"
    assert v["episodes_original"] == "第1章前世回忆"
    assert v["episodes_needs_review"]
    v=s.update_variant(v["id"],v["version"],name="改名",episodes=v["episodes"])
    assert v["episodes_needs_review"]
    v=s.update_variant(v["id"],v["version"],episodes="1")
    assert not v["episodes_needs_review"]
    assert v["episodes_original"] == "第1章前世回忆"
    with s.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM episode_migration_report").fetchone()[0] == 1
