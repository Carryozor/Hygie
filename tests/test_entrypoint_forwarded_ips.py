"""docker/entrypoint.sh must pass --forwarded-allow-ips derived from
HYGIE_TRUSTED_PROXIES (unless FORWARDED_ALLOW_IPS is already set).
A fake `uvicorn` in PATH records its argv; the real server never starts."""
import os
import stat
import subprocess
from pathlib import Path

import pytest

ENTRYPOINT = Path(__file__).resolve().parent.parent / "docker" / "entrypoint.sh"


@pytest.fixture
def run_entrypoint(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    out = tmp_path / "argv.txt"
    fake = bindir / "uvicorn"
    fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$@" > "{out}"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)

    def _run(**env):
        e = {"PATH": f"{bindir}:{os.environ['PATH']}", "HOME": str(tmp_path)}
        e.update(env)
        r = subprocess.run(["bash", str(ENTRYPOINT)], env=e, capture_output=True,
                           text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert out.exists(), "fake uvicorn was never invoked"
        return out.read_text().splitlines()

    return _run


def test_flag_present_when_trusted_proxies_set_and_spaces_stripped(run_entrypoint):
    argv = run_entrypoint(HYGIE_TRUSTED_PROXIES=" 172.27.0.0/16 , 10.0.0.1 ")
    assert "--forwarded-allow-ips" in argv
    assert argv[argv.index("--forwarded-allow-ips") + 1] == "172.27.0.0/16,10.0.0.1"
    assert argv[:2] == ["backend.main:app", "--host"]


def test_flag_absent_when_forwarded_allow_ips_already_set(run_entrypoint):
    argv = run_entrypoint(HYGIE_TRUSTED_PROXIES="172.27.0.0/16", FORWARDED_ALLOW_IPS="10.0.0.0/8")
    assert "--forwarded-allow-ips" not in argv


def test_flag_absent_when_list_empty_or_unset(run_entrypoint):
    assert "--forwarded-allow-ips" not in run_entrypoint(HYGIE_TRUSTED_PROXIES="  ")
    assert "--forwarded-allow-ips" not in run_entrypoint()
