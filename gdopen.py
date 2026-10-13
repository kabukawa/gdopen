"""Open the local path that Google Drive for desktop assigned to a Drive URL."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import winreg
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlparse

PATH_ID_PATTERNS = (
    "/file/d/",
    "/folders/",
    "/document/d/",
    "/spreadsheets/d/",
    "/presentation/d/",
    "/forms/d/",
    "/drawings/d/",
)
ALLOWED_HOSTS = {"drive.google.com", "docs.google.com"}
MAX_DEPTH = 64


@dataclass(frozen=True)
class Mount:
    account_id: str
    letter: str

    @property
    def root(self) -> Path:
        return Path(f"{self.letter}:\\")

    def metadata_db(self, drivefs_root: Path | None = None) -> Path:
        base = drivefs_root or Path(os.environ["LOCALAPPDATA"]) / "Google" / "DriveFS"
        return base / self.account_id / "metadata_sqlite_db"


@dataclass(frozen=True)
class Chain:
    """Titles from the Drive root item down to the linked item."""

    titles: tuple[str, ...]
    is_folder: bool


@dataclass(frozen=True)
class Placement:
    path: Path
    is_folder: bool


@dataclass(frozen=True)
class ResolveResult:
    found_in_index: bool
    paths: tuple[Placement, ...]
    missing: tuple[Path, ...]


def parse_file_id(text: str) -> str | None:
    raw = text.strip().strip("<>")
    if not raw:
        return None
    if "://" not in raw and "/" not in raw and "?" not in raw:
        return raw if _is_file_id(raw) else None

    parsed = urlparse(raw)
    host = parsed.netloc.lower().split(":")[0]
    if host.startswith("www."):
        host = host[4:]
    if host not in ALLOWED_HOSTS:
        return None

    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id and _is_file_id(query_id):
        return query_id

    path = parsed.path
    for marker in PATH_ID_PATTERNS:
        index = path.find(marker)
        if index == -1:
            continue
        rest = path[index + len(marker) :]
        file_id = rest.split("/", 1)[0]
        if _is_file_id(file_id):
            return file_id
    return None


def _is_file_id(value: str) -> bool:
    if len(value) < 10:
        return False
    return all(char.isalnum() or char in "_-" for char in value)


def _safe_segment(title: str) -> bool:
    if not title or title in (".", ".."):
        return False
    return "/" not in title and "\\" not in title


def load_mounts() -> list[Mount]:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Google\DriveFS") as key:
        raw, _ = winreg.QueryValueEx(key, "PerAccountPreferences")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    data = json.loads(raw)
    mounts: list[Mount] = []
    for entry in data.get("per_account_preferences", []):
        account_id = str(entry.get("key", "")).strip()
        letter = str(entry.get("value", {}).get("mount_point_path", ""))
        letter = letter.strip().rstrip("\\")
        if letter.endswith(":"):
            letter = letter[:-1]
        if account_id and len(letter) == 1 and letter.isalpha():
            mounts.append(Mount(account_id, letter.upper()))
    return mounts


def connect_readonly(db: Path) -> sqlite3.Connection:
    uri = "file:" + db.as_posix() + "?mode=ro"
    return sqlite3.connect(uri, uri=True)


def chains_for_id(connection: sqlite3.Connection, file_id: str) -> list[Chain]:
    rows = connection.execute(
        "SELECT stable_id, is_folder FROM items WHERE id = ?",
        (file_id,),
    ).fetchall()
    chains: list[Chain] = []
    seen: set[tuple[str, ...]] = set()
    for stable_id, is_folder in rows:
        for titles in _title_chains(connection, stable_id, frozenset()):
            if titles in seen:
                continue
            seen.add(titles)
            chains.append(Chain(titles, bool(is_folder)))
    return chains


def _title_chains(
    connection: sqlite3.Connection,
    stable_id: int,
    seen: frozenset[int],
) -> list[tuple[str, ...]]:
    if stable_id in seen or len(seen) >= MAX_DEPTH:
        return []
    row = connection.execute(
        "SELECT local_title FROM items WHERE stable_id = ?",
        (stable_id,),
    ).fetchone()
    if row is None or not _safe_segment(row[0]):
        return []
    title = row[0]
    parents = [
        parent
        for (parent,) in connection.execute(
            "SELECT parent_stable_id FROM stable_parents WHERE item_stable_id = ?",
            (stable_id,),
        )
    ]
    if not parents:
        return [(title,)]
    chains: list[tuple[str, ...]] = []
    next_seen = seen | {stable_id}
    for parent in parents:
        for prefix in _title_chains(connection, parent, next_seen):
            chains.append(prefix + (title,))
    return chains


def place_chain(chain: Chain, drive_root: Path) -> tuple[list[Path], list[Path]]:
    """Return existing paths and constructed paths that are not on disk."""

    if not chain.titles:
        return [], []
    head = chain.titles[0]
    existing: list[Path] = []
    missing: list[Path] = []
    try:
        names = list(drive_root.iterdir())
    except OSError:
        return [], []
    for entry in names:
        if not entry.is_dir():
            continue
        if entry.name.startswith(".") or entry.name.startswith("$"):
            continue
        if entry.name == head:
            candidate = drive_root.joinpath(*chain.titles)
        else:
            bridge = entry / head
            if not bridge.exists():
                continue
            candidate = entry.joinpath(*chain.titles)
        if candidate.exists():
            existing.append(candidate)
        else:
            missing.append(candidate)
    return existing, missing


def resolve_file_id(
    file_id: str,
    mounts: list[Mount],
    drivefs_root: Path | None = None,
) -> ResolveResult:
    found = False
    paths: list[Placement] = []
    missing: list[Path] = []
    seen_paths: set[Path] = set()
    for mount in mounts:
        db = mount.metadata_db(drivefs_root)
        if not db.is_file():
            continue
        connection = connect_readonly(db)
        try:
            chains = chains_for_id(connection, file_id)
        finally:
            connection.close()
        if not chains:
            continue
        found = True
        for chain in chains:
            existing, absent = place_chain(chain, mount.root)
            for path in existing:
                if path in seen_paths:
                    continue
                seen_paths.add(path)
                paths.append(Placement(path, path.is_dir()))
            for path in absent:
                if path not in missing:
                    missing.append(path)
    return ResolveResult(found, tuple(paths), tuple(missing))


def open_in_explorer(placement: Placement) -> None:
    path = str(placement.path)
    if placement.is_folder:
        subprocess.Popen(["explorer.exe", path])
        return
    subprocess.Popen(["explorer.exe", "/select," + path])


def run(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="gdopen",
        description="Google Drive の URL を、ドライブアプリのローカルパスで Explorer から開く。",
    )
    parser.add_argument("target", nargs="?", help="Drive の URL、またはファイル ID")
    parser.add_argument(
        "--print",
        dest="print_only",
        action="store_true",
        help="パスだけを表示し、Explorer は開かない",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="URL 解釈とパス組み立てのテストを実行する",
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return _self_test()
    if not args.target:
        print("URL またはファイル ID を指定してください。", file=sys.stderr)
        return 2
    file_id = parse_file_id(args.target)
    if file_id is None:
        print("ファイル ID を取り出せません。", file=sys.stderr)
        return 2
    try:
        mounts = load_mounts()
    except OSError:
        print("Google ドライブアプリの割り当てをレジストリから読めません。", file=sys.stderr)
        return 1
    if not mounts:
        print("ドライブアプリに割り当てられたドライブレターがありません。", file=sys.stderr)
        return 1
    result = resolve_file_id(file_id, mounts)
    if not result.found_in_index:
        print("この ID はドライブアプリの索引にありません。", file=sys.stderr)
        return 1
    if not result.paths:
        print("索引にはありましたが、ドライブ上にそのパスはありません。", file=sys.stderr)
        for path in result.missing:
            print(path, file=sys.stderr)
        return 1
    for placement in result.paths:
        print(placement.path)
    if not args.print_only:
        open_in_explorer(result.paths[0])
    return 0


def _self_test() -> int:
    failures: list[str] = []

    def check(name: str, ok: bool) -> None:
        if not ok:
            failures.append(name)

    folder_id = "folderIdExample01"
    file_id = "fileIdExample0001"
    check("folder url", parse_file_id(f"https://drive.google.com/drive/folders/{folder_id}?usp=sharing") == folder_id)
    check("file url", parse_file_id(f"https://drive.google.com/file/d/{file_id}/view") == file_id)
    check("user index", parse_file_id(f"https://drive.google.com/drive/u/1/folders/{folder_id}") == folder_id)
    check("open query", parse_file_id(f"https://drive.google.com/open?id={file_id}") == file_id)
    check("uc query", parse_file_id(f"https://drive.google.com/uc?id={file_id}&export=download") == file_id)
    check("doc", parse_file_id(f"https://docs.google.com/document/d/{file_id}/edit") == file_id)
    check("sheet", parse_file_id(f"https://docs.google.com/spreadsheets/d/{file_id}/edit#gid=0") == file_id)
    check("slide", parse_file_id(f"https://docs.google.com/presentation/d/{file_id}/edit") == file_id)
    check("form", parse_file_id(f"https://docs.google.com/forms/d/{file_id}/edit") == file_id)
    check("raw", parse_file_id(file_id) == file_id)
    check("short", parse_file_id("short") is None)
    check("other host", parse_file_id(f"https://example.com/file/d/{file_id}") is None)
    check("empty", parse_file_id("  ") is None)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        my_drive = root / "Root"
        child = my_drive / "Child"
        child.mkdir(parents=True)
        note = root / "Group" / "Team" / "Note.txt"
        note.parent.mkdir(parents=True)
        note.write_text("x", encoding="utf-8")
        hidden = root / ".hidden" / "Root"
        hidden.mkdir(parents=True)

        db = root / "metadata_sqlite_db"
        connection = sqlite3.connect(db)
        connection.executescript(
            """
            CREATE TABLE items (
                stable_id INTEGER,
                id TEXT,
                local_title TEXT,
                is_folder BOOLEAN
            );
            CREATE TABLE stable_parents (
                item_stable_id INTEGER,
                parent_stable_id INTEGER
            );
            """
        )
        connection.executemany(
            "INSERT INTO items (stable_id, id, local_title, is_folder) VALUES (?, ?, ?, ?)",
            [
                (1, "rootMyDrive00001", "Root", 1),
                (2, folder_id, "Child", 1),
                (3, "teamRootExample1", "Team", 1),
                (4, file_id, "Note.txt", 0),
            ],
        )
        connection.executemany(
            "INSERT INTO stable_parents (item_stable_id, parent_stable_id) VALUES (?, ?)",
            [(2, 1), (4, 3)],
        )
        connection.commit()
        connection.close()

        reader = connect_readonly(db)
        try:
            folder_chains = chains_for_id(reader, folder_id)
            file_chains = chains_for_id(reader, file_id)
            missing_chains = chains_for_id(reader, "missingIdExample")
        finally:
            reader.close()
        check("folder chain", folder_chains == [Chain(("Root", "Child"), True)])
        check("file chain", file_chains == [Chain(("Team", "Note.txt"), False)])
        if folder_chains:
            existing, absent = place_chain(folder_chains[0], root)
            check("my drive path", existing == [child] and absent == [])
        if file_chains:
            existing, absent = place_chain(file_chains[0], root)
            check("shared path", existing == [note] and absent == [])
        check("unknown id", missing_chains == [])

    if failures:
        print("failed: " + ", ".join(failures), file=sys.stderr)
        return 1
    print("self-test ok")
    return 0


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (OSError, ValueError):
                pass
    raise SystemExit(run(sys.argv[1:]))


if __name__ == "__main__":
    main()
