"""Downloading a folder from the file explorer as a zip.
Run: python -m pytest tests/file_explorer"""

import io
import os
import zipfile

from app.backend.zip_stream import directory_entries, zip_stream


def test_folder_zip_keeps_the_tree(tmp_path):
    root = tmp_path / "profile"
    (root / "frames" / "deep").mkdir(parents=True)
    (root / "result.h5").write_bytes(b"h5" * 5000)
    (root / "frames" / "0001.png").write_bytes(b"png")
    (root / "frames" / "deep" / "empty.txt").write_bytes(b"")
    os.symlink("/", root / "frames" / "everything")

    data = b"".join(zip_stream(directory_entries(str(root)), chunk=1024))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.testzip() is None
        assert sorted(zf.namelist()) == [
            "profile/frames/0001.png", "profile/frames/deep/empty.txt", "profile/result.h5"]
        assert zf.read("profile/result.h5") == b"h5" * 5000


def test_empty_folder_is_a_valid_zip(tmp_path):
    (tmp_path / "nothing").mkdir()
    data = b"".join(zip_stream(directory_entries(str(tmp_path / "nothing"))))
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        assert zf.namelist() == []
