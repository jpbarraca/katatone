"""--reduce-writes: remount read-write around writes, read-only after."""
from __future__ import annotations

import os
import threading

import pytest

from katatone import rofs
from katatone.auth import Auth
from katatone.gui import app as gui_app
from katatone.library.store import Library
from katatone.liveset.tsl import Tsl
from katatone.midi.mapping import Profile
from katatone.midi.store import ProfileStore


class Sys:
    """A fake mount table and ``mount`` command."""

    def __init__(self, tmp_path, ro=True, fail=(), fstype="ext4"):
        self.fstype = fstype
        self.tmp = str(tmp_path)
        self.ro, self.fail, self.calls = ro, set(fail), []
        self.info = tmp_path / "mountinfo"
        self.write_info()

    def write_info(self):
        opts = "ro" if self.ro else "rw"
        self.info.write_text(
            "20 1 8:1 / / rw,relatime - ext4 /dev/sda1 rw\n"
            f"30 20 8:2 / {self.tmp.replace(' ', chr(92) + '040')} {opts},relatime - {self.fstype} /dev/sda2 {opts}\n")

    def run(self, args):
        if args[0] == "sudo":
            args = args[2:]
        mode = args[2].split(",")[1]
        self.calls.append(mode)
        if mode in self.fail:
            return 32, "mount point is busy"
        self.ro = mode == "ro"
        self.write_info()
        return 0, ""


@pytest.fixture
def mounts(tmp_path, monkeypatch):
    d = tmp_path / "mnt"
    d.mkdir()
    s = Sys(d)
    monkeypatch.setattr(rofs._s, "run", s.run)
    monkeypatch.setattr(rofs._s, "mountinfo", str(s.info))
    monkeypatch.setattr(rofs._s, "enabled", True)
    monkeypatch.setattr(rofs._s, "writers", {})
    monkeypatch.setattr(rofs._s, "reopened", set())
    monkeypatch.setattr(rofs._s, "init_held", [])
    monkeypatch.setattr(rofs._s, "scope", {str(d)})        # the data file system
    monkeypatch.setattr(os, "sync", lambda: None)
    s.dir = d
    return s


def test_disabled_does_nothing(mounts, monkeypatch) -> None:
    monkeypatch.setattr(rofs._s, "enabled", False)
    with rofs.writable(mounts.dir / "x"):
        pass
    assert mounts.calls == []


def test_read_only_mount_is_opened_then_closed(mounts) -> None:
    with rofs.writable(mounts.dir / "not-yet" / "file"):
        assert mounts.calls == ["rw"] and not mounts.ro
    assert mounts.calls == ["rw", "ro"] and mounts.ro


def test_mount_that_was_writable_is_left_alone(mounts) -> None:
    mounts.ro = False
    mounts.write_info()
    with rofs.writable(mounts.dir):
        pass
    assert mounts.calls == []


def test_nested_writers_share_one_remount(mounts) -> None:
    with rofs.writable(mounts.dir):
        with rofs.writable(mounts.dir / "sub"):
            assert mounts.calls == ["rw"]
        assert mounts.ro is False                 # outer writer still working
    assert mounts.calls == ["rw", "ro"]


def test_concurrent_writers_stay_open_until_the_last_one(mounts) -> None:
    inner_in, outer_out = threading.Event(), threading.Event()

    def other():
        with rofs.writable(mounts.dir):
            inner_in.set()
            outer_out.wait(5)

    t = threading.Thread(target=other)
    t.start()
    inner_in.wait(5)
    with rofs.writable(mounts.dir):
        pass
    assert mounts.ro is False                     # the other writer is still in
    outer_out.set()
    t.join()
    assert mounts.calls == ["rw", "ro"]


def test_failed_rw_remount_raises_and_leaves_nothing_open(mounts) -> None:
    mounts.fail = {"rw"}
    with pytest.raises(rofs.RemountError, match="busy"):
        with rofs.writable(mounts.dir):
            pytest.fail("block must not run")
    assert not any(rofs._s.writers.values())


def test_failed_ro_remount_is_retried_then_logged(mounts, caplog) -> None:
    mounts.fail = {"ro"}
    with rofs.writable(mounts.dir):
        pass
    assert mounts.calls == ["rw", "ro", "ro"]
    assert "cannot remount" in caplog.text
    mounts.fail = set()
    with rofs.writable(mounts.dir):               # still ours to close
        pass
    assert mounts.ro


def test_unprivileged_users_go_through_sudo(mounts, monkeypatch) -> None:
    seen = []
    monkeypatch.setattr(os, "geteuid", lambda: 1000)
    monkeypatch.setattr(rofs._s, "run", lambda a: (seen.append(a), mounts.run(a))[1])
    with rofs.writable(mounts.dir):
        pass
    assert seen[0][:3] == ["sudo", "-n", "mount"]


def test_mount_with_escaped_space(tmp_path, monkeypatch) -> None:
    d = tmp_path / "with space"
    d.mkdir()
    s = Sys(d)
    monkeypatch.setattr(rofs._s, "mountinfo", str(s.info))
    assert rofs.mount_of(d / "f") == (str(d), True)


# ------------------------------------------------------------- the writers

def test_library_writes_are_wrapped(mounts) -> None:
    lib = Library(mounts.dir / "livesets")
    mounts.calls.clear()
    lib.save(Tsl(name="One"))
    assert mounts.calls == ["rw", "ro"]
    mounts.calls.clear()
    lib.rename("One", "Two")                      # one remount for save + unlink
    assert mounts.calls == ["rw", "ro"] and lib.exists("Two") and not lib.exists("One")
    mounts.calls.clear()
    lib.remove("Two")
    assert mounts.calls == ["rw", "ro"] and not lib.exists("Two") and mounts.ro


def test_reads_do_not_remount(mounts) -> None:
    lib = Library(mounts.dir / "livesets")
    lib.save(Tsl(name="One"))
    mounts.calls.clear()
    lib.list(), lib.load("One"), lib.exists("One")
    assert mounts.calls == []


def test_profile_store_writes_are_wrapped(mounts) -> None:
    st = ProfileStore(mounts.dir / "midi")
    mounts.calls.clear()
    st.save(Profile(name="Pedal"))
    st.set_active("Pedal")
    st.rename("Pedal", "Board")
    st.delete("Board")
    assert mounts.calls == ["rw", "ro"] * 4 and st.list() == [] and mounts.ro


def test_password_change_is_wrapped(mounts) -> None:
    a = Auth(mounts.dir / "data" / "auth.json")
    a.set_password("correct horse")
    assert mounts.calls == ["rw", "ro"] and a.check("correct horse")


def test_wifi_changes_remount_the_system_dirs(mounts, monkeypatch) -> None:
    from katatone.net.wifi import Wifi, WifiError
    monkeypatch.setattr(Wifi, "available", staticmethod(lambda: True))
    monkeypatch.setattr(Wifi, "dhcp_server_available", staticmethod(lambda: True))
    mounts.fail = {"rw"}
    seen = []
    monkeypatch.setattr(rofs, "mount_of", lambda p: seen.append(str(p)) or (str(mounts.dir), True))
    w = Wifi(lambda a, t=30: (0, "", ""))
    with pytest.raises(WifiError, match="remount"):
        w.configure("home", "hunter2hunter2", "", "")
    with pytest.raises(WifiError, match="remount"):
        w.set_hostname("katatone")
    assert seen == ["/etc/NetworkManager/system-connections", "/etc/hostname"]


# --------------------------------------------------------------- the flag

def test_log_flag_is_parsed_and_off_by_default() -> None:
    assert gui_app._parse_args(["--web"]).log is False
    assert gui_app._parse_args(["--web", "--log"]).log is True
    assert gui_app._log_path("x.log") is None          # nothing without --log


def test_flag_is_parsed() -> None:
    assert gui_app._parse_args(["--web"]).reduce_writes is False
    assert gui_app._parse_args(["--web", "--reduce-writes"]).reduce_writes is True


def test_logs_leave_the_data_dir_with_the_flag(mounts, tmp_path, monkeypatch) -> None:
    import tempfile
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(gui_app, "_log_files", True)
    assert gui_app._log_path("analyzer.log") == tmp_path / "katatone-analyzer.log"


# ------------------------------------------------------- the start-up phase

def test_init_holds_the_mount_writable_until_it_ends(mounts) -> None:
    rofs.begin_init()
    assert mounts.calls == ["rw"] and not mounts.ro
    lib = Library(mounts.dir / "data" / "livesets")           # start-up writes
    lib.save(Tsl(name="One"))
    assert mounts.calls == ["rw"]                              # no remount per write
    rofs.end_init()
    assert mounts.calls == ["rw", "ro"] and mounts.ro
    rofs.end_init()                                            # idempotent
    assert mounts.calls == ["rw", "ro"]


def test_after_init_writes_remount_one_by_one_again(mounts) -> None:
    rofs.begin_init()
    rofs.end_init()
    mounts.calls.clear()
    Library(mounts.dir / "livesets").save(Tsl(name="One"))
    assert mounts.calls == ["rw", "ro", "rw", "ro"]            # mkdir, then save


def test_init_makes_a_writable_mount_read_only_afterwards(mounts) -> None:
    mounts.ro = False
    mounts.write_info()
    rofs.begin_init()
    assert mounts.calls == []                                  # already writable
    rofs.end_init()
    assert mounts.calls == ["ro"] and mounts.ro


@pytest.mark.parametrize("fstype", ["tmpfs", "overlay", "ramfs"])
@pytest.mark.parametrize("read_only", [False, True])
def test_ram_backed_and_layered_mounts_are_never_touched(tmp_path, monkeypatch, fstype,
                                                         read_only) -> None:
    d = tmp_path / "m"
    d.mkdir()
    s = Sys(d, ro=read_only, fstype=fstype)
    monkeypatch.setattr(rofs._s, "run", s.run)
    monkeypatch.setattr(rofs._s, "mountinfo", str(s.info))
    monkeypatch.setattr(rofs._s, "enabled", True)
    monkeypatch.setattr(rofs._s, "writers", {})
    monkeypatch.setattr(rofs._s, "reopened", set())
    monkeypatch.setattr(rofs._s, "init_held", [])
    monkeypatch.setattr(rofs._s, "scope", {str(d)})
    monkeypatch.setattr(os, "sync", lambda: None)
    rofs.begin_init()
    with rofs.writable(d / "file"):
        pass
    rofs.end_init()
    assert s.calls == []


def test_init_failing_to_remount_is_an_error(mounts) -> None:
    mounts.fail = {"rw"}
    with pytest.raises(rofs.RemountError):
        rofs.begin_init()


def test_init_does_nothing_without_the_flag(mounts, monkeypatch) -> None:
    monkeypatch.setattr(rofs._s, "enabled", False)
    rofs.begin_init()
    rofs.end_init()
    assert mounts.calls == []


def test_main_holds_the_data_file_system_during_start_up(monkeypatch) -> None:
    got = []
    monkeypatch.setattr(rofs, "begin_init", lambda: got.append(1))
    monkeypatch.setattr(gui_app, "_end_init_after", lambda s: None)
    gui_app._begin_init(gui_app._parse_args(["--web"]))                  # no flag
    assert got == []
    gui_app._begin_init(gui_app._parse_args(["--web", "--reduce-writes", "--wifi"]))
    assert got == [1]


def test_start_up_ends_after_the_delay_with_nobody_connected(monkeypatch) -> None:
    done = threading.Event()
    monkeypatch.setattr(rofs, "end_init", done.set)
    gui_app._end_init_after(0.05)
    assert done.wait(5)


def test_the_delay_defaults_to_two_minutes_and_is_configurable(monkeypatch) -> None:
    assert gui_app._parse_args(["--web"]).rw_seconds == 120
    assert gui_app._parse_args(["--rw-seconds", "30"]).rw_seconds == 30
    seen = []
    monkeypatch.setattr(rofs, "begin_init", lambda: None)
    monkeypatch.setattr(gui_app, "_end_init_after", seen.append)
    gui_app._begin_init(gui_app._parse_args(["--web", "--reduce-writes"]))
    assert seen == [120]
    gui_app._begin_init(gui_app._parse_args(["--web"]))          # no flag: no timer
    assert seen == [120]


# ------------------------------------- only the data and /etc file systems

class ThreeMounts:
    """A read-only root (holding /etc), a data partition and an unrelated one."""

    def __init__(self, tmp_path):
        self.root = tmp_path / "root"
        self.data = self.root / "data"
        self.other = self.root / "media"
        self.data.mkdir(parents=True)
        self.other.mkdir()
        self.calls = []
        self.info = tmp_path / "mountinfo"
        self.info.write_text(
            f"20 1 8:1 / {self.root} ro,relatime - ext4 /dev/sda1 ro\n"
            f"30 20 8:2 / {self.data} ro,relatime - ext4 /dev/sda2 ro\n"
            f"31 20 8:3 / {self.other} ro,relatime - ext4 /dev/sda3 ro\n")

    def run(self, args):
        self.calls.append((args[-1], args[2].split(",")[1]))
        return 0, ""


@pytest.fixture
def two(tmp_path, monkeypatch):
    t = ThreeMounts(tmp_path)
    monkeypatch.setattr(rofs._s, "run", t.run)
    monkeypatch.setattr(rofs._s, "mountinfo", str(t.info))
    monkeypatch.setattr(rofs._s, "writers", {})
    monkeypatch.setattr(rofs._s, "reopened", set())
    monkeypatch.setattr(rofs._s, "init_held", [])
    monkeypatch.setattr(os, "sync", lambda: None)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    rofs.enable(True, t.data / "library", also=[t.root / "etc"])
    yield t
    rofs.enable(False)


def test_enable_finds_the_data_and_etc_file_systems(two) -> None:
    assert rofs._s.scope == {str(two.data), str(two.root)}     # deepest mount for each


def test_data_and_etc_are_remounted_and_nothing_else(two) -> None:
    with rofs.writable(two.data / "library" / "x.tsl", two.root / "etc" / "hostname"):
        pass
    assert sorted(two.calls) == sorted([(str(two.data), "rw"), (str(two.root), "rw"),
                                        (str(two.data), "ro"), (str(two.root), "ro")])
    two.calls.clear()
    with rofs.writable(two.other / "x"):
        pass
    assert two.calls == []


def test_start_up_holds_both_and_only_both(two) -> None:
    rofs.begin_init()
    with rofs.writable(two.other / "x", two.data / "y"):
        pass
    assert sorted(two.calls) == sorted([(str(two.data), "rw"), (str(two.root), "rw")])
    rofs.end_init()
    assert sorted(two.calls[2:]) == sorted([(str(two.data), "ro"), (str(two.root), "ro")])


def test_data_and_etc_on_one_file_system_are_remounted_once(tmp_path, monkeypatch) -> None:
    t = ThreeMounts(tmp_path)
    monkeypatch.setattr(rofs._s, "run", t.run)
    monkeypatch.setattr(rofs._s, "mountinfo", str(t.info))
    monkeypatch.setattr(rofs._s, "writers", {})
    monkeypatch.setattr(rofs._s, "reopened", set())
    monkeypatch.setattr(os, "sync", lambda: None)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    rofs.enable(True, t.root / "home" / ".local" / "share", also=[t.root / "etc"])
    try:
        assert rofs._s.scope == {str(t.root)}
        with rofs.writable(t.root / "etc" / "hostname", t.root / "home" / "x"):
            pass
        assert t.calls == [(str(t.root), "rw"), (str(t.root), "ro")]
    finally:
        rofs.enable(False)


def test_a_place_that_cannot_be_located_is_skipped_with_a_warning(two, caplog) -> None:
    rofs.enable(True, two.data / "library", also=["/nowhere/at/all"])
    assert rofs._s.scope == {str(two.data)} and "cannot tell which file system" in caplog.text


def test_unknown_mount_table_means_nothing_is_remounted(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(rofs._s, "mountinfo", str(tmp_path / "missing"))
    calls = []
    monkeypatch.setattr(rofs._s, "run", lambda a: calls.append(a) or (0, ""))
    rofs.enable(True, tmp_path)
    try:
        with rofs.writable(tmp_path / "x"):
            pass
        rofs.begin_init()
        assert calls == [] and rofs._s.scope == set()
    finally:
        rofs.enable(False)
