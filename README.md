# Shuffler

A web tool for Unraid arrays that answers one question: **where is this folder
scattered, what is duplicated, and what happens to my free space if I
consolidate it onto disk3?** Then it performs the moves safely.

Point it at a path like `/mnt/disk1/data/media` and it walks that same relative
path on **every** data disk, so you can see that `tv shows/Cowboy Bebop` has
16 GB on disk2 and 8 GB on disk3, that `S01E05.mkv` exists as a 3.8 GB copy and
an 888 MB copy, and exactly what your disks would look like after consolidating.

## What it does

- **Cross-disk index.** One scan walks the same subpath on all disks and keys
  everything by its path relative to that root. Per-directory, per-disk byte and
  file totals fall out of that.
- **Duplicate detection by path.** A file present at the same relative path on
  two or more disks is a duplicate. Split into *identical* (same size, almost
  certainly redundant) and *different sizes* (probably different encodes, worth
  a look before deleting).
- **Plan as you go.** Queue several consolidations and the array view shows a
  running projection of every disk after every job, so you can see where you
  will end up before committing to anything.
- **Explicit conflict decisions.** Any file that exists on more than one disk
  needs a decision — keep larger, keep smaller, keep a named disk, keep both
  (renamed), or leave it alone. A job cannot run until every conflict has an
  answer. Nothing is guessed on your behalf.
- **Safe execution.** Copy to the target disk, verify the destination size,
  then delete the source, then prune the directories that were emptied.
  Dry run is the default.
- **Live progress.** A bar for the queue and a bar for the file being copied
  right now, with the current transfer rate and an estimate of the time left.
  Because `cp` reports nothing while it runs, the remote copy samples the
  partial file's size twice a second rather than routing the data through this
  machine to measure it.
- **Resumable.** A job that fails partway can be retried in place. The work is
  recomputed against a fresh scan, so files that already made it across are
  simply no longer part of the job, and a file already present at the
  destination is never re-transferred.

## Safety

- **Never routes through `/mnt/user`.** Copying between disks via the fuse user
  share is a well-known way to destroy files on Unraid. Every path is checked
  and any operation touching `/mnt/user` or `/mnt/user0` is refused outright.
- **A source is only deleted after its copy verifies.** If the destination size
  does not match, the job fails and the source is left exactly where it was.
- **Copies land on a sidecar name** (`*.shuffler-partial`) and are renamed into
  place, so an interrupted run never leaves a truncated file that looks whole to
  the next scan.
- **Dry run by default.** A live run needs the phrase `MOVE MY FILES` typed in.
- **Pruning stops at the scanned root.** Empty parents are cleaned up, but a
  share folder above the scan root is left alone.
- **A reserve is honoured.** Any job that would take a disk below the configured
  free-space reserve is flagged and blocked before it runs.

## Running it

### Docker on Unraid (production)

Bind each data disk in individually, keeping its name. Do **not** mount
`/mnt/user`.

```bash
docker compose up -d --build
```

Then open `http://<tower>:8756`. See `docker-compose.yml` for the disk mounts
and environment; `/config` holds the SQLite database with your scans, queue and
settings.

Once you are happy, set `SHUFFLER_DRY_RUN=false` to make live runs the default —
they still require confirmation each time.

### Development from another machine (over SSH)

The dev machine reaches Unraid over SSH and scanning is read-only.

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt

nvm use                       # Node 20, pinned in .nvmrc
cd frontend && npm install && cd ..

# API on :8756
PYTHONPATH=backend .venv/bin/uvicorn app.main:app --reload --port 8756

# UI on :5173, proxying /api to the above
cd frontend && npm run dev
```

Set the SSH host, user and key in the UI's connection dialog (the gear icon), or
copy `.env.example` to `.env` and fill it in. A key is strongly preferred over a
password, which would be stored in the database in plain text.

Requirements on the Unraid side: SSH access and GNU `find`, which Unraid has.
Scanning uses a single `find -printf` per disk rather than thousands of stat
round-trips, so a 100k-file library is one streamed response per disk.

### Against synthetic fixtures (no hardware needed)

```bash
./scripts/dev-fixtures.sh
```

This builds a fake four-disk array of **sparse** files under
`/tmp/shuffler-fixtures` — around 105 GB apparent for about 200 KB of real disk
— and starts both servers against it. The tree deliberately includes a show
split across disks, identical duplicates, different-size duplicates, a
three-way conflict, and a folder that is already consolidated.

## Tests

```bash
.venv/bin/pytest
```

139 tests covering path normalisation, index aggregation, duplicate
classification, every conflict resolution mode, operation ordering, queue
projection arithmetic, the reserve and overlap guards, dry-run inertness, live
moves with pruning, verification failure leaving sources intact, the `/mnt/user`
refusal, retry and restart recovery, copy progress parsing and rate smoothing,
the SSH command parsers (against captured Unraid-shaped output), and the whole
HTTP surface. They run against the sparse fixtures, so the full suite takes a
few seconds.

If SSH gives you trouble, `./scripts/check-ssh.py HOST --user root --ask-password`
tests reachability, authentication, GNU `find` and disk discovery separately, so
a failure points at one specific cause.

## How it fits together

```
frontend/  React + Vite + Tailwind SPA
backend/
  app/
    backends/   StorageBackend: SSHBackend (dev) and LocalBackend (Docker)
    scan.py     cross-disk index, duplicate and fragmentation classification
    planner.py  jobs, conflict resolution, the queue simulator
    executor.py verified copy, delete, prune, SSE progress
    service.py  connection, active scan, queue, persistence
    api/        REST + the SSE progress stream
scripts/
  make_fixtures.py   synthetic sparse-file array
  dev-fixtures.sh    run everything against it
  smoke.py           end-to-end scan → plan → execute check
```

The `StorageBackend` split is the load-bearing decision: SSH for development,
local filesystem for the Docker deployment, one interface so nothing above that
layer knows the difference.

## Configuration

Everything is environment-driven with a `SHUFFLER_` prefix; see
`.env.example` for the full list. The most important ones:

| Variable | Default | Purpose |
| --- | --- | --- |
| `SHUFFLER_STORAGE_BACKEND` | `ssh` | `ssh` or `local` |
| `SHUFFLER_DRY_RUN` | `true` | Whether runs default to dry |
| `SHUFFLER_MOUNT_ROOT` | `/mnt` | Where the numbered disks live |
| `SHUFFLER_RESERVE_BYTES` | 10 GiB | Free space to keep on every disk |
| `SHUFFLER_DB_PATH` | `./data/shuffler.db` | Scans, queue and settings |
