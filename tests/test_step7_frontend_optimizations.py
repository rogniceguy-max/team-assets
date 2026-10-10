import re
from pathlib import Path
import pytest

def test_candidate_library_js_contains_set_index():
    js_path = Path("web-dock/candidate-library.js")
    assert js_path.exists()
    content = js_path.read_text(encoding="utf-8")

    # Verify candidateEntries uses Set index and O(N) path accumulation
    assert "activePaths = new Set()" in content
    assert "activePaths.has(value.path)" in content
    assert "activePaths.add(p)" in content
    # Verify the old slow nested filter is gone
    assert "files.some(item => {" not in content

def test_preview_js_video_resource_teardown():
    js_path = Path("web-dock/preview.js")
    assert js_path.exists()
    content = js_path.read_text(encoding="utf-8")

    # Verify querySelectorAll('video') is torn down on selectIndex and closeUnifiedPreview
    matches = re.findall(r"overlay\?\.querySelectorAll\('video'\)\.forEach\(v => \{.*?v\.pause\(\);.*?v\.removeAttribute\('src'\);.*?v\.load\(\);", content, re.DOTALL)
    assert len(matches) >= 2, f"Expected teardown in selectIndex and closeUnifiedPreview, found {len(matches)}"

def test_folder_active_paths_algorithm_equivalence():
    """Simulate both algorithms in Python to prove exact equivalence."""
    folders = [
        {"path": "第一季", "parent_path": ""},
        {"path": "第一季/第一集", "parent_path": "第一季"},
        {"path": "第一季/第一集/场景", "parent_path": "第一季/第一集"},
        {"path": "第一季/第二集", "parent_path": "第一季"},  # empty folder
        {"path": "第二季", "parent_path": ""},  # empty folder
        {"path": "独立素材", "parent_path": ""},
    ]

    files = [
        {"folderPath": "第一季/第一集/场景", "name": "bg1.png"},
        {"folderPath": "第一季/第一集", "name": "char1.png"},
        {"folderPath": "独立素材", "name": "item1.png"},
    ]

    current_folders_to_test = ["", "第一季", "第一季/第一集", "第一季/第二集", "第二季"]

    for current in current_folders_to_test:
        # 1. Old Algorithm (O(M*N))
        old_visible = [
            f["path"] for f in folders
            if f["parent_path"] == current and any(
                item["folderPath"] == f["path"] or item["folderPath"].startswith(f["path"] + "/")
                for item in files
            )
        ]

        # 2. New Algorithm (O(N) with Set)
        active_paths = set()
        for item in files:
            p = item["folderPath"]
            while p:
                active_paths.add(p)
                slash = p.rfind("/")
                p = "" if slash == -1 else p[:slash]

        new_visible = [
            f["path"] for f in folders
            if f["parent_path"] == current and f["path"] in active_paths
        ]

        assert old_visible == new_visible, f"Mismatch at current='{current}': old={old_visible}, new={new_visible}"
