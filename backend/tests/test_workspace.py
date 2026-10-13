import pytest

from workspace import resolve_workdir


def call(tmp_path, directory="", task="Write a snake game please now", sid="abcdef123456"):
    return resolve_workdir(directory, task, sid, tmp_path / "ws", tmp_path)


def test_default_is_a_private_per_task_folder_under_the_workspace_root(tmp_path):
    d = call(tmp_path)
    assert d.is_dir() and d.parent == (tmp_path / "ws").resolve()
    assert d.name == "write-a-snake-game-abcdef"


def test_tilde_path_is_expanded_against_home_and_created(tmp_path):
    d = call(tmp_path, "~/code/snake")
    assert d == (tmp_path / "code" / "snake").resolve() and d.is_dir()


def test_relative_path_means_relative_to_home(tmp_path):
    assert call(tmp_path, "projects/x") == (tmp_path / "projects" / "x").resolve()


def test_absolute_path_inside_home_is_allowed(tmp_path):
    assert call(tmp_path, str(tmp_path / "a" / "b")).is_dir()


@pytest.mark.parametrize("bad", ["/etc", "/tmp/../etc", "~/../escape", "../../etc"])
def test_paths_outside_home_are_refused_and_not_created(tmp_path, bad):
    with pytest.raises(ValueError, match="home"):
        call(tmp_path, bad)


def test_symlink_pointing_outside_home_is_refused(tmp_path):
    outside = tmp_path.parent / "outside-target"
    outside.mkdir(exist_ok=True)
    (tmp_path / "link").symlink_to(outside)
    with pytest.raises(ValueError, match="home"):
        call(tmp_path, "~/link/sub")


def test_empty_or_symbol_only_task_still_gets_a_valid_name(tmp_path):
    assert call(tmp_path, task="???").name == "task-abcdef"
