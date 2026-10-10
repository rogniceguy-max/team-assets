import hashlib
import io
import sys
import zipfile
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "mcp-client" / "scripts"))
from mcp_client import updater as updater_module
from mcp_client.updater import UpdaterMixin


def client_package(version="9.9.9"):
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("team-assets-mcp-client/SKILL.md", "# updated skill")
        archive.writestr("team-assets-mcp-client/VERSION", version)
        archive.writestr("team-assets-mcp-client/scripts/team_assets_mcp.py", "print('updated')")
    return output.getvalue()


class FakeUpdater(UpdaterMixin):
    def __init__(self, package):
        self.package = package
        self.digest = hashlib.sha256(package).hexdigest()

    def call_tool(self, name):
        assert name == "get_skill_manifest"
        return {
            "version": "9.9.9",
            "sha256": self.digest,
            "download_url": "http://test.invalid/client.zip",
        }

    def _upload_url(self, url):
        return url

    def download_client_package(self, url):
        return self.package


def test_update_removes_files_absent_from_new_package(tmp_path):
    target = tmp_path / "mcp-client"
    target.mkdir()
    (target / "VERSION").write_text("1.0.0", encoding="utf-8")
    stale = target / "scripts" / "obsolete_module.py"
    stale.parent.mkdir()
    stale.write_text("old code", encoding="utf-8")

    result = FakeUpdater(client_package()).update_client_package(package_dir=target, force=True)

    assert result["updated"] is True
    assert (target / "VERSION").read_text(encoding="utf-8") == "9.9.9"
    assert (target / "scripts" / "team_assets_mcp.py").is_file()
    assert not stale.exists()


def test_failed_directory_swap_restores_previous_package(tmp_path, monkeypatch):
    target = tmp_path / "mcp-client"
    target.mkdir()
    (target / "VERSION").write_text("1.0.0", encoding="utf-8")
    old_file = target / "scripts" / "old_module.py"
    old_file.parent.mkdir()
    old_file.write_text("old code", encoding="utf-8")

    original_replace = updater_module.os.replace
    failed = False

    def fail_install_once(source, destination):
        nonlocal failed
        if Path(destination) == target and ".new-" in Path(source).name and not failed:
            failed = True
            raise OSError("simulated new package install failure")
        return original_replace(source, destination)

    monkeypatch.setattr(updater_module.os, "replace", fail_install_once)
    with pytest.raises(OSError, match="simulated new package install failure"):
        FakeUpdater(client_package()).update_client_package(package_dir=target, force=True)

    assert failed
    assert (target / "VERSION").read_text(encoding="utf-8") == "1.0.0"
    assert old_file.read_text(encoding="utf-8") == "old code"
    assert not (target / "SKILL.md").exists()
    assert not list(tmp_path.glob(".mcp-client.new-*"))
