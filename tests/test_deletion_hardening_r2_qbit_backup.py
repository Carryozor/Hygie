"""R2 — B4 (qBit disappearance check: 5 attempts, growing waits) and
B5 (backup _publish / stale .part cleanup)."""
import errno
import os
import time

import pytest

import backend.backup as backup
import backend.qbit_client as qbit


class _R:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else []

    def json(self):
        return self._payload


def _req_factory(info_payloads):
    infos = list(info_payloads)
    calls = {"info": 0}

    async def _req(method, path, **kw):
        if path == "/api/v2/torrents/delete":
            return _R(200)
        calls["info"] += 1
        return _R(200, infos.pop(0) if len(infos) > 1 else infos[0])
    return _req, calls


# ── B4 ───────────────────────────────────────────────────────────────────────

async def test_b4_five_attempts_with_growing_waits_then_false(monkeypatch):
    slept = []

    async def _sleep(d):
        slept.append(d)
    monkeypatch.setattr(qbit.asyncio, "sleep", _sleep)
    req, calls = _req_factory([[{"hash": "deadbeef"}]])
    monkeypatch.setattr(qbit, "_request", req)
    assert await qbit.qbit_delete_torrent("DEADBEEF") is False
    assert calls["info"] == 5
    assert slept == [0.5, 1, 2, 3]


async def test_b4_true_as_soon_as_hash_disappears(monkeypatch):
    slept = []

    async def _sleep(d):
        slept.append(d)
    monkeypatch.setattr(qbit.asyncio, "sleep", _sleep)
    req, calls = _req_factory([[{"hash": "deadbeef"}], [{"hash": "deadbeef"}], []])
    monkeypatch.setattr(qbit, "_request", req)
    assert await qbit.qbit_delete_torrent("DEADBEEF") is True
    assert calls["info"] == 3
    assert slept == [0.5, 1]


# ── B5 ───────────────────────────────────────────────────────────────────────

def _good_tmp(tmp_path):
    p = tmp_path / "scratch.part"
    p.write_bytes(b"x" * 64)
    return str(p)


def test_b5a_remove_failure_after_link_does_not_fail(tmp_path, monkeypatch):
    tmp = _good_tmp(tmp_path)
    monkeypatch.setattr(backup, "_verify_backup_file", lambda p: True)
    real_remove = os.remove

    def _boom(p):
        if p == tmp:
            raise PermissionError("nope")
        real_remove(p)
    monkeypatch.setattr(backup.os, "remove", _boom)
    name = backup._publish(tmp, str(tmp_path), "20260101_000000", ".db")
    assert name == "hygie_20260101_000000.db"
    assert (tmp_path / name).exists()


@pytest.mark.parametrize("code", [errno.EPERM, errno.EXDEV, errno.ENOTSUP,
                                  errno.EOPNOTSUPP, errno.EMLINK])
def test_b5c_replace_fallback_on_hardlink_unsupported(tmp_path, monkeypatch, code):
    tmp = _good_tmp(tmp_path)
    monkeypatch.setattr(backup, "_verify_backup_file", lambda p: True)

    def _link(a, b):
        raise OSError(code, "no hardlinks")
    monkeypatch.setattr(backup.os, "link", _link)
    name = backup._publish(tmp, str(tmp_path), "20260101_000000", ".db")
    assert (tmp_path / name).exists() and not os.path.exists(tmp)


def test_b5c_other_oserror_does_not_replace(tmp_path, monkeypatch):
    tmp = _good_tmp(tmp_path)
    monkeypatch.setattr(backup, "_verify_backup_file", lambda p: True)

    def _link(a, b):
        raise OSError(errno.EIO, "disk")
    monkeypatch.setattr(backup.os, "link", _link)
    with pytest.raises(OSError):
        backup._publish(tmp, str(tmp_path), "20260101_000000", ".db")
    assert os.path.exists(tmp)


def test_b5c_existing_final_never_overwritten(tmp_path, monkeypatch):
    tmp = _good_tmp(tmp_path)
    monkeypatch.setattr(backup, "_verify_backup_file", lambda p: True)
    (tmp_path / "hygie_20260101_000000.db").write_bytes(b"old")
    name = backup._publish(tmp, str(tmp_path), "20260101_000000", ".db")
    assert name == "hygie_20260101_000000_1.db"
    assert (tmp_path / "hygie_20260101_000000.db").read_bytes() == b"old"


def test_b5b_stale_part_files_removed_only_when_old(tmp_path):
    old = tmp_path / "hygie_a.1234.db.part"
    fresh = tmp_path / "hygie_b.5678.db.part"
    other = tmp_path / "hygie_20260101_000000.db"
    unrelated = tmp_path / "foo.part"
    for f in (old, fresh, other, unrelated):
        f.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))
    os.utime(unrelated, (past, past))
    os.utime(other, (past, past))
    backup._purge_stale_parts(str(tmp_path))
    assert not old.exists()
    assert fresh.exists() and other.exists() and unrelated.exists()


async def test_b5b_create_backup_purges_stale_parts(tmp_path, monkeypatch):
    old = tmp_path / "hygie_a.1234.db.part"
    old.write_bytes(b"x")
    past = time.time() - 7200
    os.utime(old, (past, past))

    async def _fail(*a, **k):
        raise RuntimeError("stop here")
    monkeypatch.setattr(backup, "_sqlite_backup", _fail)
    monkeypatch.setattr(backup, "_mariadb_backup", _fail)

    async def _log(*a, **k):
        return None
    monkeypatch.setattr(backup, "add_log", _log)
    assert await backup._create_backup(str(tmp_path), 7) is None
    assert not old.exists()
