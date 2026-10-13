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
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "VI_PKG_CHECK": "not-installed-tool"}
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env), home


def clean_repo(tmp_path):
    """Temp copy of just what install.sh needs: no config.toml, no .env."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir()
    shutil.copy(SCRIPT, repo / "install.sh")
    shutil.copy(REPO / "config.example.toml", repo / "config.example.toml")
    return repo


def run_clean(tmp_path, *args, extra_env=None):
    repo = clean_repo(tmp_path)
    rel = tmp_path / "os-release"
    rel.write_text('ID=arch\nNAME="arch"\n')
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "VI_PKG_CHECK": "not-installed-tool", **(extra_env or {})}
    return subprocess.run(["bash", str(repo / "install.sh"), *args], capture_output=True, text=True, env=env), home


def test_clean_checkout_dry_run_finishes(tmp_path):
    r, _ = run_clean(tmp_path, "--dry-run")
    assert r.returncode == 0, r.stderr
    assert "create config.toml from config.example.toml" in r.stdout
    assert 'Done. Say "Hey Nova"' in r.stdout
    assert "systemctl --user restart voice-instinct.service" in r.stdout  # update re-run restarts the backend


def test_uv_resolved_from_local_bin_after_install(tmp_path):
    r, home = run_clean(tmp_path, "--dry-run", extra_env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert "uv" in r.stdout and f"{home}/.local/bin" in r.stdout


def uninstall_fixture(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    sc = fake / "systemctl"
    sc.write_text("#!/bin/sh\nexit 1\n")
    sc.chmod(0o755)
    home = tmp_path / "home"
    for f in (".config/systemd/user/voice-instinct.service", ".config/autostart/voice-instinct.desktop", ".local/bin/voice-instinct", ".voice-instinct/models/x"):
        (home / f).parent.mkdir(parents=True, exist_ok=True)
        (home / f).write_text("x")
    rel = tmp_path / "os-release"
    rel.write_text('ID=arch\nNAME="arch"\n')
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "VI_PKG_CHECK": "not-installed-tool", "PATH": f"{fake}:{os.environ['PATH']}"}
    return home, env


def test_uninstall_is_idempotent_when_systemctl_fails(tmp_path):
    home, env = uninstall_fixture(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT), "--uninstall"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not (home / ".config/systemd/user/voice-instinct.service").exists()
    assert not (home / ".config/autostart/voice-instinct.desktop").exists()
    assert not (home / ".local/bin/voice-instinct").exists()
    assert (home / ".voice-instinct/models/x").exists()


def test_uninstall_purge_removes_data_even_when_systemctl_fails(tmp_path):
    home, env = uninstall_fixture(tmp_path)
    r = subprocess.run(["bash", str(SCRIPT), "--uninstall", "--purge"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert not (home / ".voice-instinct").exists()


def test_append_env_adds_missing_newline(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("A=1")  # no trailing newline
    fn = subprocess.run(["sed", "-n", "/^append_env()/,/^}/p", str(SCRIPT)], capture_output=True, text=True).stdout
    assert fn, "append_env() missing"
    subprocess.run(["bash", "-c", f'{fn}\nappend_env "$1" KEY val', "_", str(env_file)], check=True)
    assert env_file.read_text() == "A=1\nKEY=val\n"


def test_dry_run_on_arch_plans_everything_and_changes_nothing(tmp_path):
    r, home = run(tmp_path, "arch", "--dry-run")
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "pacman -S --needed bubblewrap socat curl tar bzip2 xz" in out and "nodejs" not in out.split("System packages")[1].split("\n")[0]
    assert "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2" in out and "kokoro-v1.0.onnx" in out
    assert "uv sync --python 3.13" in out and "npm ci" in out  # unpinned, uv picks 3.15 on a clean box: no onnxruntime wheels
    assert "electron/install.js" in out  # npm 12 skips install scripts, so fetch the binary explicitly
    assert "voice-instinct.service" in out and "voice-instinct.desktop" in out
    assert list(home.iterdir()) == []  # a dry run creates nothing


def test_debian_and_fedora_use_their_package_managers(tmp_path):
    d = run(tmp_path, "debian", "--dry-run")[0].stdout
    f = run(tmp_path, "fedora", "--dry-run")[0].stdout
    assert "apt-get install -y bubblewrap socat curl tar bzip2 xz-utils" in d
    assert "dnf install -y bubblewrap socat curl tar bzip2 xz" in f
    assert "nodejs" not in d.replace("nodejs.org", "") and "npm install" not in d


def fake_uname(tmp_path, machine):
    bin_ = tmp_path / "fakebin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "uname").write_text(f"#!/bin/sh\necho {machine}\n")
    (bin_ / "uname").chmod(0o755)
    return {"PATH": f"{bin_}:{os.environ['PATH']}"}


@pytest.mark.parametrize("machine,arch", [("x86_64", "x64"), ("aarch64", "arm64")])
def test_pinned_node_is_downloaded_for_the_arch(tmp_path, machine, arch):
    r, _ = run_clean(tmp_path, "--dry-run", extra_env=fake_uname(tmp_path, machine))
    assert r.returncode == 0, r.stderr
    assert f"https://nodejs.org/dist/v22.23.3/node-v22.23.3-linux-{arch}.tar.xz" in r.stdout
    assert "tar -xJf" in r.stdout
    assert 'PATH="' in r.stdout and "/.voice-instinct/node" in r.stdout and "npm ci" in r.stdout


def test_unsupported_cpu_stops_before_any_package_step(tmp_path):
    r, _ = run_clean(tmp_path, "--dry-run", extra_env=fake_uname(tmp_path, "riscv64"))
    assert r.returncode != 0 and "unsupported CPU" in (r.stdout + r.stderr)
    assert "pacman" not in r.stdout


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
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "VI_PKG_CHECK": "not-installed-tool", "SWAYSOCK": "/x", "PATH": f"{fake}:{os.environ['PATH']}"}
    out = subprocess.run(["bash", str(SCRIPT), "--dry-run"], capture_output=True, text=True, env=env).stdout
    size = int(next(l.split("=")[1].split("#")[0] for l in (REPO / "config.example.toml").read_text().splitlines() if l.startswith("size")))
    if (REPO / "config.toml").exists():
        size = int(next((l.split("=")[1].split("#")[0] for l in (REPO / "config.toml").read_text().splitlines() if l.startswith("size")), size))
    assert f"resize set {size} {size}, move position {2560 - size - 24} 12" in out
    assert "bindsym $mod+n exec voice-instinct toggle" in out
    assert "exec voice-instinct" in out.replace("exec voice-instinct toggle", "")  # Sway has no XDG autostart


@pytest.mark.skipif(shutil.which("shellcheck") is None, reason="shellcheck not installed")
def test_shellcheck_is_clean():
    r = subprocess.run(["shellcheck", "-S", "warning", str(SCRIPT)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_no_answer_at_the_sudo_prompt_means_no(tmp_path):
    # stdin closed (CI, curl | bash): the installer must not go ahead and run sudo.
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "sudo").write_text(f"#!/bin/sh\ntouch {tmp_path}/sudo-ran\n")
    (fake / "sudo").chmod(0o755)
    for tool in ("curl", "systemctl", "uv", "npm"):  # if the prompt ever says yes again, fail fast, touch nothing real
        (fake / tool).write_text("#!/bin/sh\nexit 1\n")
        (fake / tool).chmod(0o755)
    rel = tmp_path / "os-release"
    rel.write_text('ID=arch\nNAME="arch"\n')
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "VI_PKG_CHECK": "not-installed-tool", "PATH": f"{fake}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode != 0 and not (tmp_path / "sudo-ran").exists()
    assert "sudo pacman -S --needed" in r.stderr  # tells the user what to run instead


def test_launcher_starts_electron_without_a_system_node(tmp_path):
    # Debian and Fedora desktops have no `node`; the installer's pinned Node is only on PATH for the build.
    src = SCRIPT.read_text()
    body = src.split('write "$LAUNCHER" 755 <<EOF\n', 1)[1].split("\nEOF\n", 1)[0]
    repo = tmp_path / "repo"
    fe = repo / "frontend" / "node_modules"
    (fe / "electron" / "dist").mkdir(parents=True)
    (fe / ".bin").mkdir()
    (fe / "electron" / "cli.js").write_text("#!/usr/bin/env node\n")
    (fe / "electron" / "cli.js").chmod(0o755)
    (fe / ".bin" / "electron").symlink_to("../electron/cli.js")
    ran = tmp_path / "ran"
    (fe / "electron" / "dist" / "electron").write_text(f'#!/bin/sh\necho "$@" > {ran}\n')
    (fe / "electron" / "dist" / "electron").chmod(0o755)
    launcher = tmp_path / "voice-instinct"
    launcher.write_text(body.replace("\\$", "$").replace("$REPO", str(repo)))
    empty = tmp_path / "nopath"
    empty.mkdir()
    r = subprocess.run(["/bin/bash", str(launcher), "toggle"], capture_output=True, text=True, env={"PATH": str(empty)})
    assert r.returncode == 0, r.stderr
    assert ran.read_text().split() == [str(repo / "frontend"), "--toggle"]


def test_rerun_with_every_package_present_does_not_ask_for_sudo(tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "sudo").write_text(f"#!/bin/sh\ntouch {tmp_path}/sudo-ran\n")
    for tool in ("sudo", "bwrap", "socat", "tar", "bzip2", "xz"):
        if tool != "sudo":
            (fake / tool).write_text("#!/bin/sh\nexit 0\n")
        (fake / tool).chmod(0o755)
    for tool in ("curl", "systemctl", "uv", "npm"):  # stop right after the package step, touch nothing real
        (fake / tool).write_text("#!/bin/sh\nexit 1\n")
        (fake / tool).chmod(0o755)
    rel = tmp_path / "os-release"
    rel.write_text('ID=arch\nNAME="arch"\n')
    home = tmp_path / "home"
    home.mkdir()
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "PATH": f"{fake}:/usr/bin:/bin"}
    r = subprocess.run(["bash", str(SCRIPT)], capture_output=True, text=True, env=env, stdin=subprocess.DEVNULL)
    assert "Not installing system packages" not in r.stderr and not (tmp_path / "sudo-ran").exists()
    assert "already installed" in r.stdout


@pytest.mark.parametrize("restricted", ["1", "0"])
def test_electron_sandbox_helper_is_set_up_where_user_namespaces_are_restricted(tmp_path, restricted):
    # Ubuntu 24.04's AppArmor blocks unprivileged user namespaces; Electron then needs its SUID helper.
    userns = tmp_path / "userns"
    userns.write_text(restricted + "\n")
    r, _ = run_clean(tmp_path, "--dry-run", extra_env={"VI_USERNS_FILE": str(userns)})
    helper = "frontend/node_modules/electron/dist/chrome-sandbox"
    assert ("sudo chmod 4755" in r.stdout and helper in r.stdout) == (restricted == "1")
