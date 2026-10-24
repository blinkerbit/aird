"""Chat attach uploads land on disk as the body arrives."""

import os
import tempfile

from aird.plugins.chat.handlers import ChatAttachHandler
from aird.plugins.chat.upload_stream import parse_spooled_form


def test_data_received_spools_chunks_to_disk():
    handler = ChatAttachHandler.__new__(ChatAttachHandler)
    fd, path = tempfile.mkstemp(prefix="aird_chat_body_")
    os.close(fd)
    handler._body_path = path
    handler._body_file = open(path, "wb")
    try:
        handler.data_received(b"abc")
        handler.data_received(b"def")
        handler._close_body()
        with open(path, "rb") as src:
            assert src.read() == b"abcdef"
    finally:
        handler._close_body()
        handler._unlink_body()


def test_parse_spooled_form_file_bytes_stay_on_disk():
    boundary = "----aird"
    payload = b"x" * (70 * 1024) + b"tail"
    head = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="source_path"\r\n\r\n'
        "docs/a.txt\r\n"
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="big.bin"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode()
    body = head + payload + f"\r\n--{boundary}--\r\n".encode()
    fd, path = tempfile.mkstemp(prefix="aird_chat_body_")
    os.close(fd)
    with open(path, "wb") as out:
        for start in range(0, len(body), 1000):
            out.write(body[start : start + 1000])
    try:
        fields, files = parse_spooled_form(
            path, f"multipart/form-data; boundary={boundary}"
        )
    finally:
        os.unlink(path)
    assert fields["source_path"] == ["docs/a.txt"]
    assert len(files) == 1
    meta = files[0]
    assert meta["filename"] == "big.bin"
    assert meta["body"] == b""
    tmp = meta["tmp_path"]
    try:
        with open(tmp, "rb") as src:
            assert src.read() == payload
    finally:
        os.unlink(tmp)
