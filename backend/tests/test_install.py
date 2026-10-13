import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "install.sh"


def run(tmp_path, os_id, *args):
    rel = tmp_path / "os-release"
    rel.write_text(f'ID={os_id}\nNAME="{os_id}"\n')
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home)}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env), home


def test_dry_run_on_arch_plans_everything_and_changes_nothing(tmp_path):
    r, home = run(tmp_path, "arch", "--dry-run")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "pacman -S --needed bubblewrap socat nodejs npm curl tar bzip2" in out
    assert "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2" in out and "kokoro-v1.0.onnx" in out
    assert "uv sync" in out and "npm ci" in out
    assert "electron/install.js" in out  # npm 12 skips install scripts, so fetch the binary explicitly
    assert "voice-instinct.service" in out and "voice-instinct.desktop" in out
    assert list(home.iterdir()) == []  # a dry run creates nothing


def test_debian_and_fedora_use_their_package_managers(tmp_path):
    assert "apt-get install -y bubblewrap socat nodejs npm" in run(tmp_path, "debian", "--dry-run")[0].stdout
    assert "dnf install -y bubblewrap socat nodejs npm" in run(tmp_path, "fedora", "--dry-run")[0].stdout


def test_unsupported_distro_stops_before_any_change(tmp_path):
    r, home = run(tmp_path, "gentoo", "--dry-run")
    assert r.returncode != 0 and "not supported" in (r.stdout + r.stderr)
    assert "pacman" not in r.stdout and "apt-get" not in r.stdout


def test_existing_user_config_and_env_are_never_overwritten(tmp_path):
    cfg = REPO / "config.toml"
    existed = cfg.exists()
    r, _ = run(tmp_path, "arch", "--dry-run")
    if existed:
        assert "keep existing config.toml" in r.stdout
    else:
        assert "create config.toml from config.example.toml" in r.stdout


def test_uninstall_keeps_data_unless_purge(tmp_path):
    keep = run(tmp_path, "arch", "--uninstall", "--dry-run")[0].stdout
    purge = run(tmp_path, "arch", "--uninstall", "--purge", "--dry-run")[0].stdout
    assert "voice-instinct.service" in keep and "rm -rf" not in keep
    assert "remove data" not in keep and "remove data" in purge and "rm -rf" in purge


def test_sway_rule_uses_gui_size_and_output_width(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    sm = fake / "swaymsg"
    sm.write_text('#!/bin/sh\necho \'[{"focused":false,"rect":{"width":800}},{"focused":true,"rect":{"width":2560}}]\'\n')
    sm.chmod(0o755)
    rel = tmp_path / "os-release"
    rel.write_text('ID=arch\nNAME="arch"\n')
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "SWAYSOCK": "/x", "PATH": f"{fake}:{os.environ['PATH']}"}
    out = subprocess.run(["bash", str(SCRIPT), "--dry-run"], capture_output=True, text=True, env=env).stdout
    size = int(next(l.split("=")[1].split("#")[0] for l in (REPO / "config.example.toml").read_text().splitlines() if l.startswith("size")))
    if (REPO / "config.toml").exists():
        size = int(next((l.split("=")[1].split("#")[0] for l in (REPO / "config.toml").read_text().splitlines() if l.startswith("size")), size))
    assert f"resize set {size} {size}, move position {2560 - size - 24} 12" in out
    assert "bindsym $mod+n exec voice-instinct toggle" in out


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    r = subprocess.run(["shellcheck", "-S", "warning", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout
