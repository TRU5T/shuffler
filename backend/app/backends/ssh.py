"""SSH backend used during development against a live Unraid box.

The performance-critical decision here is that a full tree walk is a *single*
remote command per disk (`find -printf`) whose null-delimited output is streamed
back, rather than thousands of individual stat round-trips. A 100k-file library
scans in one request per disk.
"""

from __future__ import annotations

import posixpath
import shlex
import threading
from collections.abc import Callable, Iterator

import paramiko

from ..models import Disk, FileEntry
from .base import CopyProgress, StorageBackend, StorageError, assert_safe_path

#: Null-delimited so filenames containing newlines cannot corrupt the stream.
FIND_FILES = "find {root} -type f -printf '%s\\t%Ts\\t%P\\0'"
FIND_CHILDREN = "find {path} -mindepth 1 -maxdepth 1 -printf '%y\\t%f\\0'"

#: How often to prove a long-running copy's connection is still alive. Without
#: this, a blocking read on a dead peer would hang forever.
KEEPALIVE_SECONDS = 30

#: How often the remote copy reports the partial file's size.
POLL_SECONDS = 0.5

#: Line markers used by the copy script. `cp` says nothing while it runs, so
#: progress is sampled from the partial file and tagged for parsing.
TICK = "P"
DONE = "D"


class _Unset:
    """Distinguishes "use the default timeout" from an explicit ``None``."""


UNSET = _Unset()


def describe(exc: BaseException) -> str:
    """Render an exception usefully.

    `socket.timeout` and several paramiko errors stringify to the empty string,
    which turns a failure report into "remote command failed: ".
    """
    text = str(exc).strip()
    return text or type(exc).__name__


class SSHBackend(StorageBackend):
    def __init__(
        self,
        host: str,
        port: int = 22,
        user: str = "root",
        password: str = "",
        key_path: str = "",
        timeout: int = 20,
        mount_root: str = "/mnt",
        disk_pattern: str = r"^disk\d+$",
    ) -> None:
        super().__init__(mount_root=mount_root, disk_pattern=disk_pattern)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_path = key_path
        self.timeout = timeout
        self._client: paramiko.SSHClient | None = None
        self._lock = threading.RLock()

    # --- connection -------------------------------------------------------

    def _connect(self) -> paramiko.SSHClient:
        if not self.host:
            raise StorageError("no SSH host configured")
        if not self.password and not self.key_path:
            raise StorageError(
                "no SSH password or key configured. Enter a password, or point "
                "Shuffler at a private key file."
            )
        client = paramiko.SSHClient()
        client.load_system_host_keys()
        # A homelab tool pointed at a LAN box: auto-accepting the host key keeps
        # first-run setup friction-free.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        where = f"{self.user}@{self.host}:{self.port}"
        kwargs: dict = {
            "hostname": self.host,
            "port": self.port,
            "username": self.user,
            "timeout": self.timeout,
            "banner_timeout": self.timeout,
            "auth_timeout": self.timeout,
            "look_for_keys": not self.password,
            "allow_agent": not self.password,
        }
        if self.key_path:
            kwargs["key_filename"] = self.key_path
        if self.password:
            kwargs["password"] = self.password
        try:
            client.connect(**kwargs)
        except paramiko.AuthenticationException as exc:
            if self.password:
                try:
                    return self._connect_keyboard_interactive()
                except StorageError:
                    raise StorageError(
                        f"{where}: the password was rejected. Check the username "
                        f"(Unraid's SSH account is usually 'root') and that the "
                        f"password matches the one you log into the web UI with."
                    ) from exc
            raise StorageError(
                f"{where}: key authentication failed ({exc}). "
                f"Check that the key is installed in ~/.ssh/authorized_keys on the server."
            ) from exc
        except paramiko.SSHException as exc:
            raise StorageError(f"{where}: SSH negotiation failed: {exc}") from exc
        except OSError as exc:
            # Covers DNS failure, refused connections and timeouts, which are
            # the mistakes worth distinguishing from a bad password.
            raise StorageError(
                f"{where} is unreachable: {exc}. Check the address, that the "
                f"box is on, and that SSH is enabled in Unraid under "
                f"Settings -> Management Access."
            ) from exc
        except Exception as exc:
            raise StorageError(f"{where}: SSH connection failed: {describe(exc)}") from exc
        self._arm_keepalive(client)
        return client

    @staticmethod
    def _arm_keepalive(client: paramiko.SSHClient) -> None:
        transport = client.get_transport()
        if transport is not None:
            transport.set_keepalive(KEEPALIVE_SECONDS)

    def _connect_keyboard_interactive(self) -> paramiko.SSHClient:
        """Retry auth via keyboard-interactive, answering every prompt with the password.

        Some sshd configurations disable `password` auth but leave
        `keyboard-interactive` on, which looks identical to a wrong password
        from the client side.
        """
        transport = paramiko.Transport((self.host, self.port))
        transport.banner_timeout = self.timeout
        try:
            transport.start_client(timeout=self.timeout)
            transport.auth_interactive(
                self.user, lambda _title, _instructions, prompts: [self.password] * len(prompts)
            )
        except Exception as exc:
            transport.close()
            raise StorageError(str(exc)) from exc
        if not transport.is_authenticated():
            transport.close()
            raise StorageError("keyboard-interactive authentication failed")
        transport.set_keepalive(KEEPALIVE_SECONDS)
        client = paramiko.SSHClient()
        client._transport = transport  # noqa: SLF001 - reuse the authenticated transport
        return client

    def _ensure(self) -> paramiko.SSHClient:
        with self._lock:
            if self._client is not None:
                transport = self._client.get_transport()
                if transport is not None and transport.is_active():
                    return self._client
                self.close()
            self._client = self._connect()
            return self._client

    def close(self) -> None:
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    pass
                self._client = None

    # --- command execution ------------------------------------------------

    def _run(
        self, command: str, timeout: int | None | _Unset = UNSET
    ) -> tuple[int, str, str]:
        """Run a command and collect its output.

        `timeout` defaults to the configured value; pass an explicit ``None`` to
        block indefinitely, which is what a long file copy needs. The
        distinction matters because paramiko's timeout applies to *reads*, and a
        `cp` of a large file produces no output at all while it runs.
        """
        client = self._ensure()
        effective = self.timeout if isinstance(timeout, _Unset) else timeout
        with self._lock:
            try:
                _, stdout, stderr = client.exec_command(command, timeout=effective)
                out = stdout.read().decode("utf-8", "replace")
                err = stderr.read().decode("utf-8", "replace")
                rc = stdout.channel.recv_exit_status()
            except Exception as exc:
                self.close()
                raise StorageError(f"remote command failed: {describe(exc)}") from exc
        return rc, out, err

    def _run_lines(self, command: str, on_line: Callable[[str], None]) -> tuple[int, str]:
        """Run a command, delivering each stdout line as it arrives.

        Used by the copy, which reports progress as it goes and so cannot wait
        for the command to finish before its output is read. There is no read
        timeout for the same reason `_run` allows one to be waived.
        """
        client = self._ensure()
        with self._lock:
            try:
                _, stdout, stderr = client.exec_command(command, timeout=None)
                channel = stdout.channel
                buffer = b""
                while True:
                    chunk = channel.recv(1 << 16)
                    if not chunk:
                        break
                    buffer += chunk
                    *lines, buffer = buffer.split(b"\n")
                    for line in lines:
                        text = line.strip().decode("utf-8", "replace")
                        if text:
                            on_line(text)
                tail = buffer.strip().decode("utf-8", "replace")
                if tail:
                    on_line(tail)
                rc = channel.recv_exit_status()
                err = stderr.read().decode("utf-8", "replace")
            except Exception as exc:
                self.close()
                raise StorageError(f"remote command failed: {describe(exc)}") from exc
        return rc, err

    def _stream_nul(self, command: str) -> Iterator[str]:
        """Run a command and yield its null-delimited output records."""
        client = self._ensure()
        with self._lock:
            try:
                _, stdout, _ = client.exec_command(command, timeout=None)
                channel = stdout.channel
                buffer = b""
                while True:
                    chunk = channel.recv(1 << 16)
                    if not chunk:
                        break
                    buffer += chunk
                    *records, buffer = buffer.split(b"\0")
                    for record in records:
                        if record:
                            yield record.decode("utf-8", "replace")
                if buffer.strip(b"\n"):
                    yield buffer.decode("utf-8", "replace")
                channel.recv_exit_status()
            except Exception as exc:
                self.close()
                raise StorageError(f"remote walk failed: {describe(exc)}") from exc

    # --- interface --------------------------------------------------------

    def check(self) -> str:
        rc, out, err = self._run("uname -sr; find --version 2>/dev/null | head -1")
        if rc != 0 and not out:
            raise StorageError(err.strip() or "remote shell returned no output")
        lines = [line for line in out.splitlines() if line.strip()]
        uname = lines[0] if lines else "unknown host"
        if len(lines) < 2 or "GNU" not in lines[1]:
            raise StorageError(
                f"connected to {uname}, but GNU find was not detected. "
                "Shuffler relies on `find -printf` for fast scanning."
            )
        disks = self.list_disks()
        if not disks:
            raise StorageError(
                f"connected to {uname}, but no data disks were found under "
                f"{self.mount_root}. On Unraid the disks live under /mnt "
                f"(disk1, disk2, ...) - check the 'Disk location' setting."
            )
        return f"{self.user}@{self.host} ({uname}), {len(disks)} disk(s)"

    def list_disks(self) -> list[Disk]:
        rc, out, err = self._run(f"ls -1 {shlex.quote(self.mount_root)}")
        if rc != 0:
            raise StorageError(err.strip() or f"cannot list {self.mount_root}")
        names = [n.strip() for n in out.splitlines() if self.is_data_disk(n.strip())]
        if not names:
            return []

        # Emitting the queried path alongside each df line keeps the mapping
        # unambiguous even if a disk happens to be unmounted.
        paths = [self.disk_path(n) for n in names]
        loop = " ".join(
            f"printf '%s\\t' {shlex.quote(p)}; df -B1 -P {shlex.quote(p)} 2>/dev/null | tail -1;"
            for p in paths
        )
        rc, out, _ = self._run(f"for x in 1; do {loop} done")

        usage: dict[str, tuple[int, int, int]] = {}
        for line in out.splitlines():
            if "\t" not in line:
                continue
            path, _, rest = line.partition("\t")
            fields = rest.split()
            if len(fields) < 4:
                continue
            try:
                total, used, avail = int(fields[1]), int(fields[2]), int(fields[3])
            except ValueError:
                continue
            usage[path.strip()] = (total, used, avail)

        disks = []
        for name in names:
            path = self.disk_path(name)
            if path not in usage:
                continue
            total, used, avail = usage[path]
            disks.append(Disk(name=name, path=path, total=total, used=used, free=avail))
        return self.sort_disks(disks)

    def walk(self, disk: str, subpath: str) -> Iterator[tuple[str, FileEntry]]:
        root = self.full_path(disk, subpath)
        command = FIND_FILES.format(root=shlex.quote(root)) + " 2>/dev/null"
        for record in self._stream_nul(command):
            parts = record.split("\t", 2)
            if len(parts) != 3:
                continue
            size_s, mtime_s, relpath = parts
            if not relpath:
                continue
            try:
                yield relpath, FileEntry(size=int(size_s), mtime=float(mtime_s))
            except ValueError:
                continue

    def list_dir(self, path: str) -> list[tuple[str, bool]]:
        command = FIND_CHILDREN.format(path=shlex.quote(path)) + " 2>/dev/null"
        out: list[tuple[str, bool]] = []
        for record in self._stream_nul(command):
            kind, _, name = record.partition("\t")
            if name:
                out.append((name, kind == "d"))
        return out

    def exists(self, path: str) -> bool:
        rc, _, _ = self._run(f"test -e {shlex.quote(path)}")
        return rc == 0

    def size_of(self, path: str) -> int | None:
        rc, out, _ = self._run(f"stat -c %s {shlex.quote(path)} 2>/dev/null")
        if rc != 0:
            return None
        try:
            return int(out.strip())
        except ValueError:
            return None

    def makedirs(self, path: str) -> None:
        assert_safe_path(path)
        rc, _, err = self._run(f"mkdir -p -- {shlex.quote(path)}")
        if rc != 0:
            raise StorageError(f"mkdir failed for {path}: {err.strip()}")

    def copy_file(self, src: str, dst: str, size: int, progress: CopyProgress | None = None) -> int:
        assert_safe_path(src)
        assert_safe_path(dst)
        if progress:
            progress(0, size)
        tmp = f"{dst}.shuffler-partial"
        q_src, q_dst, q_tmp = shlex.quote(src), shlex.quote(dst), shlex.quote(tmp)
        q_dir = shlex.quote(posixpath.dirname(dst))
        # Copy to a sidecar name, prove the sizes match, then rename into place.
        # The rename is atomic because both names live on the same disk.
        #
        # The leading check makes a retry cheap: if a previous attempt copied
        # this file but died before the source could be deleted, the work is
        # already done and re-copying gigabytes would be pointless.
        #
        # `cp` runs in the background so the partial file's size can be sampled
        # while it works. `cp` itself has no progress output, and doing the
        # transfer through this client instead would drag the data across the
        # network twice.
        script = (
            f"set -e; mkdir -p -- {q_dir}; "
            f"a=$(stat -c %s -- {q_src}); "
            f'if [ "$a" = "$(stat -c %s -- {q_dst} 2>/dev/null)" ]; then echo "{DONE} $a"; exit 0; fi; '
            f"cp --preserve=timestamps,mode -- {q_src} {q_tmp} & "
            "pid=$!; "
            'while kill -0 "$pid" 2>/dev/null; do '
            f'echo "{TICK} $(stat -c %s -- {q_tmp} 2>/dev/null || echo 0)"; '
            f"sleep {POLL_SECONDS}; "
            "done; "
            'wait "$pid"; '
            f"b=$(stat -c %s -- {q_tmp}); "
            f'if [ "$a" != "$b" ]; then rm -f -- {q_tmp}; echo "size mismatch $a != $b" >&2; exit 3; fi; '
            f'mv -f -- {q_tmp} {q_dst}; echo "{DONE} $(stat -c %s -- {q_dst})"'
        )

        written: int | None = None

        def on_line(line: str) -> None:
            nonlocal written
            marker, _, value = line.partition(" ")
            try:
                count = int(value.strip())
            except ValueError:
                return
            if marker == TICK:
                if progress:
                    progress(min(count, size), size)
            elif marker == DONE:
                written = count

        rc, err = self._run_lines(script, on_line)
        if rc != 0:
            raise StorageError(f"copy failed for {src} -> {dst}: {err.strip() or f'exit {rc}'}")
        if written is None:
            raise StorageError(f"copy of {src} produced no verifiable size")
        if progress:
            progress(written, size)
        return written

    def delete_file(self, path: str) -> None:
        assert_safe_path(path)
        rc, _, err = self._run(f"rm -f -- {shlex.quote(path)}")
        if rc != 0:
            raise StorageError(f"delete failed for {path}: {err.strip()}")

    def prune_empty_dirs(self, root: str) -> list[str]:
        assert_safe_path(root)
        command = f"find {shlex.quote(root)} -type d -empty -print -delete 2>/dev/null"
        rc, out, _ = self._run(command, timeout=None)
        return [line for line in out.splitlines() if line.strip()]

    def remove_dir(self, path: str) -> bool:
        assert_safe_path(path)
        rc, _, _ = self._run(f"rmdir -- {shlex.quote(path)} 2>/dev/null")
        return rc == 0
