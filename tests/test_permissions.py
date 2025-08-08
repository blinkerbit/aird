import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from aird.main import ROLE_PERMISSIONS


def test_viewer_cannot_modify():
    perms = ROLE_PERMISSIONS["viewer"]
    assert perms.get("download")
    assert not perms.get("upload")
    assert not perms.get("delete")
    assert not perms.get("rename")
    assert not perms.get("edit")


def test_editor_can_edit():
    perms = ROLE_PERMISSIONS["editor"]
    assert perms.get("upload")
    assert perms.get("delete")
    assert perms.get("rename")
    assert perms.get("edit")
