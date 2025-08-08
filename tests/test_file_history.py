import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parents[1]))
from aird import main


def test_backup_file(tmp_path):
    main.ROOT_DIR = tmp_path
    main.FEATURE_FLAGS["file_history"] = True

    target = tmp_path / "example.txt"
    target.write_text("hello")

    main.backup_file("example.txt")

    history_dir = tmp_path / main.HISTORY_DIR_NAME
    backups = list(history_dir.glob("example.txt.*"))
    assert backups, "Backup was not created"
