import httpx

from vtc.moodle.sync import _download_if_changed, _strip_token_query


def test_sync_skips_unchanged_file(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    target = output / "week1.pdf"
    target.write_bytes(b"hello")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"hello", headers={"content-type": "application/pdf"})
        )
    )
    result = _download_if_changed(
        client,
        "https://example.test/week1.pdf",
        output,
        "week1.pdf",
        {"files": {"week1.pdf": {"size": 5}}},
    )
    assert result["action"] == "skipped"
    assert result["reason"] == "unchanged"


def test_sync_downloads_when_size_changes(tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "week1.pdf").write_bytes(b"old")
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"newer", headers={"content-type": "application/pdf"})
        )
    )
    result = _download_if_changed(
        client,
        "https://example.test/week1.pdf",
        output,
        "week1.pdf",
        {"files": {"week1.pdf": {"size": 3}}},
    )
    assert result["action"] == "downloaded"
    assert (output / "week1.pdf").read_bytes() == b"newer"


def test_strip_token_query_does_not_keep_wstoken():
    url = "https://moodle2627.vtc.edu.hk/webservice/pluginfile.php/1/mod_resource/content/0/a.pdf?token=secret"
    assert "token=" not in _strip_token_query(url)
    assert _strip_token_query(url).endswith("/a.pdf")
