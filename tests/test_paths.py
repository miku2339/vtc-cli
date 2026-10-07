import pytest


def test_private_json_write_preserves_previous_file_on_failure(tmp_path, monkeypatch):
    from vtc.paths import write_private_json

    path = tmp_path / "session.storage.json"
    path.write_text("previous", encoding="utf-8")
    path.chmod(0o600)

    def fail_replace(*_args):
        raise OSError("synthetic interruption")

    monkeypatch.setattr("vtc.paths.os.replace", fail_replace)
    with pytest.raises(OSError):
        write_private_json(path, {"cookies": []})
    assert path.read_text(encoding="utf-8") == "previous"
    assert list(tmp_path.iterdir()) == [path]


def test_private_json_write_is_private_before_replacement(tmp_path, monkeypatch):
    import json
    import os

    from vtc.paths import write_private_json

    path = tmp_path / "session.storage.json"
    real_replace = os.replace
    modes = []

    def check_replace(source, target):
        modes.append(os.stat(source).st_mode & 0o777)
        real_replace(source, target)

    monkeypatch.setattr("vtc.paths.os.replace", check_replace)
    write_private_json(path, {"cookies": []})
    assert modes == [0o600]
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text(encoding="utf-8")) == {"cookies": []}
