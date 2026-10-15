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


def clean_repo(tmp_path, config=None):
    """Temp copy of just what install.sh needs: no config.toml (unless given), no .env."""
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    (repo / "frontend").mkdir()
    shutil.copy(SCRIPT, repo / "install.sh")
    shutil.copy(REPO / "config.example.toml", repo / "config.example.toml")
    if config is not None:
        (repo / "config.toml").write_text(config)
    return repo


def run_clean(tmp_path, *args, extra_env=None, config=None):
    repo = clean_repo(tmp_path, config)
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
    assert "nemo_en_titanet_small.onnx" in r.stdout  # speaker model (#32)
    assert "uv run enroll.py" not in r.stdout  # the default engine (Nemotron) has no voice check


def test_the_enrol_hint_is_shown_with_parakeet(tmp_path):
    r, _ = run_clean(tmp_path, "--dry-run", config='[stt]\nengine = "parakeet"\n')
    assert r.returncode == 0, r.stderr
    assert "uv run enroll.py" in r.stdout


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
    assert "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25.tar.bz2" in out and "kokoro-v1.0.onnx" in out
    assert "uv sync --python 3.13" in out and "npm ci" in out  # unpinned, uv picks 3.15 on a clean box: no onnxruntime wheels
    assert "electron/install.js" in out  # npm 12 skips install scripts, so fetch the binary explicitly
    assert "voice-instinct.service" in out and "voice-instinct.desktop" in out
    assert list(home.iterdir()) == []  # a dry run creates nothing


def test_debian_and_fedora_use_their_package_managers(tmp_path):
    d = run(tmp_path, "debian", "--dry-run")[0].stdout
    f = run(tmp_path, "fedora", "--dry-run")[0].stdout
    assert "apt-get install -y bubblewrap socat curl tar bzip2 xz-utils" in d
    assert "dnf install -y bubblewrap socat curl tar bzip2 xz" in f
    apt = next(l for l in d.splitlines() if "apt-get install" in l)
    assert "nodejs" not in apt and "npm" not in apt  # the distro's Node is too old; ours is pinned


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
    assert "bindsym $mod+n exec " in out and "/.local/bin/voice-instinct toggle" in out
    assert "/.local/bin/voice-instinct\n" in out  # Sway has no XDG autostart: exec the launcher


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


def render_unit(tmp_path, claude_bin):
    src = SCRIPT.read_text()
    body = src.split('write "$UNIT" 644 <<EOF\n', 1)[1].split("\nEOF\n", 1)[0]
    script = tmp_path / "render.sh"
    script.write_text(f'REPO=/r; uvbin=/u; HOME_DIR=/h; CLAUDE_BIN="{claude_bin}"\ncat <<EOF\n{body}\nEOF\n')
    return subprocess.run(["bash", str(script)], capture_output=True, text=True, check=True).stdout


def test_the_service_is_told_where_claude_is_even_outside_the_default_path(tmp_path):
    # npm-global or nvm installs live outside the unit's PATH; the installer's own shell can see them.
    unit = render_unit(tmp_path, "/home/u/.nvm/bin/claude")
    assert "Environment=VI_CLAUDE=/home/u/.nvm/bin/claude" in unit
    path = next(l for l in unit.splitlines() if l.startswith("Environment=PATH="))
    assert path.endswith(":/home/u/.nvm/bin")  # an npm-installed claude is a node script: node sits beside it
    assert "VI_CLAUDE" not in render_unit(tmp_path, "")


def test_install_starts_the_orb_in_a_desktop_session_and_says_how_otherwise(tmp_path):
    r, home = run_clean(tmp_path, "--dry-run", extra_env={"WAYLAND_DISPLAY": "wayland-1"})
    launcher = str(home / ".local/bin/voice-instinct")
    assert f"would run: setsid -f {launcher}" in r.stdout
    r2, home2 = run_clean(tmp_path / "x", "--dry-run", extra_env={"WAYLAND_DISPLAY": "", "DISPLAY": ""})
    assert "setsid" not in r2.stdout and f"Start the orb with {home2 / '.local/bin/voice-instinct'}" in r2.stdout


def test_the_sway_hint_uses_the_launchers_full_path(tmp_path):
    # ~/.local/bin is often not on the compositor's PATH.
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "swaymsg").write_text('#!/bin/sh\necho \'[{"focused":true,"rect":{"width":1280}}]\'\n')
    (fake / "swaymsg").chmod(0o755)
    r, home = run_clean(tmp_path, "--dry-run", extra_env={"SWAYSOCK": "/x", "PATH": f"{fake}:{os.environ['PATH']}"})
    assert f"exec {home / '.local/bin/voice-instinct'} toggle" in r.stdout


def test_launcher_quit_asks_the_running_app_to_quit(tmp_path):
    src = SCRIPT.read_text()
    body = src.split('write "$LAUNCHER" 755 <<EOF\n', 1)[1].split("\nEOF\n", 1)[0]
    repo = tmp_path / "repo"
    dist = repo / "frontend" / "node_modules" / "electron" / "dist"
    dist.mkdir(parents=True)
    ran = tmp_path / "ran"
    (dist / "electron").write_text(f'#!/bin/sh\necho "$@" > {ran}\n')
    (dist / "electron").chmod(0o755)
    launcher = tmp_path / "voice-instinct"
    launcher.write_text(body.replace("\\$", "$").replace("$REPO", str(repo)))
    subprocess.run(["/bin/bash", str(launcher), "quit"], check=True)
    assert ran.read_text().split() == [str(repo / "frontend"), "--quit"]


def test_uninstall_quits_the_running_orb_first(tmp_path):
    home, env = uninstall_fixture(tmp_path)
    out = subprocess.run(["bash", str(SCRIPT), "--uninstall", "--dry-run"], capture_output=True, text=True, env=env).stdout
    assert f"would run: {home}/.local/bin/voice-instinct quit" in out


def test_gnome_is_told_the_tray_needs_the_appindicator_extension(tmp_path):
    r, _ = run_clean(tmp_path, "--dry-run", extra_env={"XDG_CURRENT_DESKTOP": "ubuntu:GNOME", "SWAYSOCK": ""})
    assert "AppIndicator" in r.stdout and "voice-instinct quit" in r.stdout


def test_install_sets_up_the_pinned_errand_browser(tmp_path):
    r, home = run_clean(tmp_path, "--dry-run")
    assert "@playwright/mcp@0.0.83" in r.stdout and f"{home}/.voice-instinct/browser" in r.stdout
    assert f"PLAYWRIGHT_BROWSERS_PATH='{home}/.voice-instinct/browser/browsers'" in r.stdout  # purge removes it all
    assert "install chromium" in r.stdout


def test_an_installed_errand_browser_is_kept_on_re_run(tmp_path):
    # A re-run (an update) must not reinstall it, or fail offline before the service is refreshed.
    home = tmp_path / "home"
    m = home / ".voice-instinct/browser/node_modules/@playwright/mcp"
    m.mkdir(parents=True)
    (m / "package.json").write_text('{"version": "0.0.83"}')
    (home / ".voice-instinct/browser/browsers/chromium-1247").mkdir(parents=True)
    r, _ = run_clean(tmp_path, "--dry-run")
    assert "keep errand browser" in r.stdout and "install chromium" not in r.stdout


def run_with_engine(tmp_path, engine):
    repo = clean_repo(tmp_path)
    if engine is not None:
        (repo / "config.toml").write_text(f'[stt]\nengine = "{engine}"\n')
    rel = tmp_path / "os-release"
    rel.write_text('ID=arch\nNAME="arch"\n')
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {**os.environ, "VI_OS_RELEASE": str(rel), "VI_HOME": str(home), "VI_PKG_CHECK": "not-installed-tool"}
    return subprocess.run(["bash", str(repo / "install.sh"), "--dry-run"], capture_output=True, text=True, env=env)


NEMOTRON = "sherpa-onnx-nemotron-speech-streaming-en-0.6b-160ms-int8-2026-04-25.tar.bz2"
MULTI = "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-160ms-int8-2026-06-11.tar.bz2"
PARAKEET = "sherpa-onnx-nemo-parakeet-tdt-0.6b-v3-int8.tar.bz2"


@pytest.mark.parametrize("engine,wanted", [(None, NEMOTRON), ("nemotron", NEMOTRON),
                                           ("nemotron-multilingual", MULTI), ("parakeet", PARAKEET)])
def test_only_the_chosen_speech_model_is_downloaded(tmp_path, engine, wanted):
    r = run_with_engine(tmp_path, engine)
    assert r.returncode == 0, r.stderr
    assert wanted in r.stdout
    assert [m for m in (NEMOTRON, MULTI, PARAKEET) if m in r.stdout] == [wanted]


def test_an_unknown_speech_engine_stops_before_anything_is_installed(tmp_path):
    r = run_with_engine(tmp_path, "whisper")
    assert r.returncode == 1
    assert "[stt] engine must be one of nemotron, nemotron-multilingual, parakeet" in r.stderr
    assert "System packages" not in r.stdout and "would run" not in r.stdout
