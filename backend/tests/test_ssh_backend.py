"""SSH backend parsing, exercised without a real host.

The remote commands are the part most likely to break silently, so the parsers
are tested against captured Unraid-shaped output.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.backends.base import StorageError
from app.backends.ssh import UNSET, SSHBackend, _Unset, describe


class FakeSSHBackend(SSHBackend):
    """An SSHBackend with the transport replaced by canned responses."""

    def __init__(self, responses: dict[str, tuple[int, str, str]], streams: dict[str, list[str]]):
        super().__init__(host="tower.local", mount_root="/mnt")
        self.responses = responses
        self.streams = streams
        self.commands: list[str] = []
        self.timeouts: list[object] = []

    def _match(self, command: str, table: dict):
        self.commands.append(command)
        for needle, value in table.items():
            if needle in command:
                return value
        return None

    def _run(self, command: str, timeout=UNSET):  # type: ignore[override]
        self.timeouts.append(self.timeout if isinstance(timeout, _Unset) else timeout)
        result = self._match(command, self.responses)
        return result if result is not None else (0, "", "")

    def _run_lines(self, command: str, on_line):  # type: ignore[override]
        self.timeouts.append(None)
        rc, out, err = self._match(command, self.responses) or (0, "", "")
        for line in out.splitlines():
            if line.strip():
                on_line(line.strip())
        return rc, err

    def _stream_nul(self, command: str) -> Iterator[str]:  # type: ignore[override]
        yield from self._match(command, self.streams) or []


DF_OUTPUT = "\n".join(
    [
        "/mnt/disk1\t/dev/md1p1 8001427243008 6402851794944 1598575448064 81% /mnt/disk1",
        "/mnt/disk2\t/dev/md2p1 8001427243008 3201425897472 4800001345536 41% /mnt/disk2",
        "/mnt/disk10\t/dev/md10p1 4000713621504 2000356810752 2000356810752 50% /mnt/disk10",
    ]
)


def test_disks_are_discovered_and_sorted_numerically() -> None:
    backend = FakeSSHBackend(
        responses={
            "ls -1 /mnt": (0, "disk1\ndisk2\ndisk10\ncache\nuser\ndisks\n", ""),
            "df -B1": (0, DF_OUTPUT, ""),
        },
        streams={},
    )
    disks = backend.list_disks()

    # disk10 must sort after disk2, and user/cache/disks are not data disks.
    assert [d.name for d in disks] == ["disk1", "disk2", "disk10"]
    assert disks[0].total == 8001427243008
    assert disks[0].used == 6402851794944
    assert disks[0].free == 1598575448064
    assert disks[2].path == "/mnt/disk10"


def test_an_unmounted_disk_is_left_out_rather_than_guessed() -> None:
    backend = FakeSSHBackend(
        responses={
            "ls -1 /mnt": (0, "disk1\ndisk2\n", ""),
            # disk2 produced no df line at all.
            "df -B1": (0, "/mnt/disk1\t/dev/md1p1 100 40 60 40% /mnt/disk1\n/mnt/disk2\t", ""),
        },
        streams={},
    )
    assert [d.name for d in backend.list_disks()] == ["disk1"]


def test_walk_parses_size_mtime_and_relative_path() -> None:
    records = [
        "3984588800\t1712000000\ttv shows/Cowboy Bebop/Season 01/S01E05.mkv",
        "931135488\t1712000001\tmovies/Arrival (2016)/Arrival.mkv",
        "0\t1712000002\tempty.nfo",
    ]
    backend = FakeSSHBackend(responses={}, streams={"find /mnt/disk3/data/media": records})

    found = dict(backend.walk("disk3", "data/media"))
    assert found["tv shows/Cowboy Bebop/Season 01/S01E05.mkv"].size == 3984588800
    assert found["tv shows/Cowboy Bebop/Season 01/S01E05.mkv"].mtime == 1712000000
    assert found["empty.nfo"].size == 0
    assert len(found) == 3


def test_walk_tolerates_awkward_names_and_junk_records() -> None:
    records = [
        "100\t1\tshow/Season 1/ep - 1080p [x265].mkv",
        "200\t2\tshow/tab\tin name.mkv",
        "not-a-size\t3\tbroken.mkv",
        "300\t4\t",
        "garbage-with-no-tabs",
    ]
    backend = FakeSSHBackend(responses={}, streams={"find": records})
    found = dict(backend.walk("disk1", "data"))

    assert found["show/Season 1/ep - 1080p [x265].mkv"].size == 100
    # `%P` is the last field, so an embedded tab stays part of the name.
    assert found["show/tab\tin name.mkv"].size == 200
    assert "broken.mkv" not in found
    assert len(found) == 2


def test_walk_uses_one_command_per_disk() -> None:
    backend = FakeSSHBackend(responses={}, streams={"find": ["10\t1\ta.mkv", "20\t2\tb/c.mkv"]})
    list(backend.walk("disk1", "data/media"))
    assert len(backend.commands) == 1
    assert "-printf" in backend.commands[0]
    assert "/mnt/disk1/data/media" in backend.commands[0]


def test_list_dir_separates_directories_from_files() -> None:
    backend = FakeSSHBackend(
        responses={}, streams={"find": ["d\tSeason 01", "f\tposter.jpg", "l\tlink"]}
    )
    assert backend.list_dir("/mnt/disk1/data") == [
        ("Season 01", True),
        ("poster.jpg", False),
        ("link", False),
    ]


def test_paths_are_quoted_so_spaces_survive() -> None:
    backend = FakeSSHBackend(responses={"stat -c %s": (0, "42\n", "")}, streams={})
    assert backend.size_of("/mnt/disk1/tv shows/a b.mkv") == 42
    assert "'/mnt/disk1/tv shows/a b.mkv'" in backend.commands[0]


def test_a_missing_file_reports_no_size() -> None:
    backend = FakeSSHBackend(responses={"stat -c %s": (1, "", "No such file")}, streams={})
    assert backend.size_of("/mnt/disk1/gone.mkv") is None


def test_copy_verifies_the_destination_and_reports_bytes_written() -> None:
    backend = FakeSSHBackend(responses={"cp --preserve": (0, "D 3984588800\n", "")}, streams={})
    seen: list[tuple[int, int]] = []
    written = backend.copy_file(
        "/mnt/disk2/a.mkv",
        "/mnt/disk1/a.mkv",
        3984588800,
        lambda done, total: seen.append((done, total)),
    )

    assert written == 3984588800
    assert seen[0] == (0, 3984588800) and seen[-1] == (3984588800, 3984588800)
    script = backend.commands[0]
    # Copy to a sidecar, compare sizes, then rename into place.
    assert ".shuffler-partial" in script
    assert "stat -c %s" in script
    assert "mv -f" in script


def test_a_copy_reports_progress_as_the_file_grows() -> None:
    """`cp` is silent, so the partial file's size is sampled while it runs."""
    output = "P 0\nP 1000000\nP 2500000\nP 3900000\nD 4000000\n"
    backend = FakeSSHBackend(responses={"cp --preserve": (0, output, "")}, streams={})
    seen: list[int] = []

    written = backend.copy_file(
        "/mnt/disk2/a.mkv", "/mnt/disk1/a.mkv", 4000000, lambda done, _total: seen.append(done)
    )

    assert written == 4000000
    assert seen == [0, 0, 1000000, 2500000, 3900000, 4000000]
    assert seen == sorted(seen), "progress must never go backwards"


def test_a_partial_size_beyond_the_expected_total_is_clamped() -> None:
    """A file that grew mid-copy must not drive a progress bar past 100%."""
    backend = FakeSSHBackend(
        responses={"cp --preserve": (0, "P 9999\nD 100\n", "")}, streams={}
    )
    seen: list[int] = []
    backend.copy_file(
        "/mnt/disk2/a.mkv", "/mnt/disk1/a.mkv", 100, lambda done, _total: seen.append(done)
    )
    assert max(seen) == 100


def test_copy_progress_ignores_unparsable_chatter() -> None:
    backend = FakeSSHBackend(
        responses={"cp --preserve": (0, "cp: warning\nP 50\nD 100\n", "")}, streams={}
    )
    seen: list[int] = []
    written = backend.copy_file(
        "/mnt/disk2/a.mkv", "/mnt/disk1/a.mkv", 100, lambda done, _total: seen.append(done)
    )
    assert written == 100
    assert seen == [0, 50, 100]


def test_a_copy_that_never_confirms_a_size_is_an_error() -> None:
    backend = FakeSSHBackend(responses={"cp --preserve": (0, "P 50\n", "")}, streams={})
    with pytest.raises(StorageError, match="no verifiable size"):
        backend.copy_file("/mnt/disk2/a.mkv", "/mnt/disk1/a.mkv", 100)


def test_a_failed_copy_raises_with_the_remote_error() -> None:
    backend = FakeSSHBackend(
        responses={"cp --preserve": (3, "", "size mismatch 100 != 90")}, streams={}
    )
    with pytest.raises(StorageError, match="size mismatch"):
        backend.copy_file("/mnt/disk2/a.mkv", "/mnt/disk1/a.mkv", 100)


def test_writes_to_the_user_share_are_refused_before_any_command_runs() -> None:
    backend = FakeSSHBackend(responses={}, streams={})
    with pytest.raises(StorageError, match="never the /mnt/user share"):
        backend.copy_file("/mnt/disk1/a.mkv", "/mnt/user/data/a.mkv", 10)
    with pytest.raises(StorageError, match="never the /mnt/user share"):
        backend.delete_file("/mnt/user/data/a.mkv")
    assert backend.commands == [], "issued a remote command for a forbidden path"


def test_check_requires_gnu_find() -> None:
    busybox = FakeSSHBackend(responses={"uname": (0, "Linux 6.1.0\nBusyBox v1.36\n", "")}, streams={})
    with pytest.raises(StorageError, match="GNU find"):
        busybox.check()


def test_prune_reports_what_it_removed() -> None:
    backend = FakeSSHBackend(
        responses={
            "-empty": (0, "/mnt/disk2/data/media/tv/Show/Season 01\n/mnt/disk2/data/media/tv/Show\n", "")
        },
        streams={},
    )
    removed = backend.prune_empty_dirs("/mnt/disk2/data/media/tv/Show")
    assert removed == [
        "/mnt/disk2/data/media/tv/Show/Season 01",
        "/mnt/disk2/data/media/tv/Show",
    ]


def test_prune_empty_parents_stops_at_the_boundary() -> None:
    backend = FakeSSHBackend(responses={"rmdir": (0, "", "")}, streams={})
    removed = backend.prune_empty_parents(
        "/mnt/disk2/data/media/tv shows/Cowboy Bebop", "/mnt/disk2/data/media"
    )
    assert removed == [
        "/mnt/disk2/data/media/tv shows/Cowboy Bebop",
        "/mnt/disk2/data/media/tv shows",
    ]


def test_prune_empty_parents_stops_at_the_first_non_empty_directory() -> None:
    backend = FakeSSHBackend(responses={"rmdir": (1, "", "Directory not empty")}, streams={})
    assert backend.prune_empty_parents("/mnt/disk2/data/media/tv", "/mnt/disk2/data/media") == []


def test_a_copy_runs_without_a_read_timeout() -> None:
    """A multi-gigabyte copy is silent while it runs.

    Paramiko's timeout applies to reads, so any finite value aborts a copy that
    takes longer than it, even though the transfer is progressing normally.
    """
    backend = FakeSSHBackend(responses={"cp --preserve": (0, "D 3984588800\n", "")}, streams={})
    backend.copy_file("/mnt/disk2/a.mkv", "/mnt/disk1/a.mkv", 3984588800)
    assert backend.timeouts == [None]


def test_ordinary_commands_keep_the_configured_timeout() -> None:
    backend = FakeSSHBackend(responses={"stat -c %s": (0, "42\n", "")}, streams={})
    backend.size_of("/mnt/disk1/a.mkv")
    assert backend.timeouts == [backend.timeout]


def test_pruning_is_not_cut_short_either() -> None:
    backend = FakeSSHBackend(responses={"-empty": (0, "", "")}, streams={})
    backend.prune_empty_dirs("/mnt/disk2/data/media/tv/Show")
    assert backend.timeouts == [None]


def test_a_copy_whose_destination_already_matches_is_not_repeated() -> None:
    """Retrying an interrupted job must not re-transfer completed files."""
    backend = FakeSSHBackend(responses={"cp --preserve": (0, "D 3984588800\n", "")}, streams={})
    backend.copy_file("/mnt/disk2/a.mkv", "/mnt/disk1/a.mkv", 3984588800)
    script = backend.commands[0]
    assert script.index("stat -c %s -- /mnt/disk1/a.mkv") < script.index("cp --preserve")
    assert "exit 0" in script


def test_silent_failures_are_named_rather_than_blank() -> None:
    """socket.timeout stringifies to "", which makes for a useless error."""
    assert describe(TimeoutError()) == "TimeoutError"
    assert describe(StorageError("  ")) == "StorageError"
    assert describe(StorageError("disk full")) == "disk full"


def test_the_default_timeout_sentinel_is_not_a_real_value() -> None:
    assert isinstance(UNSET, _Unset)
    assert UNSET is not None
