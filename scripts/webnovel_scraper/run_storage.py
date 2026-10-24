"""Safe allocation of new run folders (v0.3.0 Checkpoint 03.0).

A new run reserves ``<root>/<canonical-novel-slug>-N`` with an exclusive ``mkdir``: the
operating system lets exactly one caller create a given name, so concurrent threads or
processes can never claim the same folder and an existing folder, file or link is never
overwritten or reused. Numbering is per novel and independent of the website. A folder
that was created but never completed stays where it is and is skipped, never reused.

A *preview* only reads the folder listing and reserves nothing. Reopening an earlier run
is a separate, validated action (:func:`open_existing_run`) for the future Resume feature.

Nothing here is wired into the GUI or pipeline yet: ``pipeline.resolve_output_dir`` still
names today's runs until allocation and run metadata are integrated together (03.3).
"""

from __future__ import annotations

import json
import os
import re
import stat
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from .atomic_io import write_json_atomic
from .source_identity import SourceIdentityError, validate_novel_slug

OUTPUT_ROOT_NAME = "Web-Novel-Scraper-Outputs"
RESERVATION_FILENAME = "run-reservation.json"
RESERVATION_KIND = "web-novel-scraper-run"
RESERVATION_SCHEMA = 1
MAX_RUN_NUMBER = 9999

_RUN_NAME_RE = re.compile(r"(?P<slug>[a-z0-9]+(?:-[a-z0-9]+)*)-(?P<number>[1-9][0-9]*)", re.IGNORECASE)


class RunStorageError(RuntimeError):
    """A run folder could not be allocated or validated (message is user-actionable)."""


def checked_output_path(run_dir: Path, relative: str) -> Path:
    """Reject links/reparse points at every component inside an output run."""
    root, rel = Path(run_dir), Path(relative)
    if rel.is_absolute() or len(rel.parts) not in (1, 2) or rel.parts[0] not in ("PDF", "TXT", "chapter-bodies") or any(
        part in (".", "..") for part in rel.parts
    ):
        raise RunStorageError(f"Invalid output path: {relative!r}.")
    path = root
    for part in (None, *rel.parts):
        if part is not None:
            path /= part
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
            raise RunStorageError(f"Linked/reparse output paths are not supported: {path}.")
    if not path.resolve().is_relative_to(root.resolve()):
        raise RunStorageError(f"Output path escapes its run folder: {path}.")
    return path


@dataclass(frozen=True)
class RunDirectory:
    """A reserved or validated run folder: ``<root>/<novel_slug>-<number>``."""

    path: Path
    novel_slug: str
    number: int


def default_output_root() -> Path:
    """``~/Downloads/Web-Novel-Scraper-Outputs`` (the home folder if there is no Downloads).

    Not created here. A redirected Windows Downloads folder is a documented limitation.
    """
    home = Path.home()
    downloads = home / "Downloads"
    return (downloads if downloads.is_dir() else home) / OUTPUT_ROOT_NAME


def _checked_slug(novel_slug: object) -> str:
    try:
        return validate_novel_slug(novel_slug)
    except SourceIdentityError as exc:
        raise RunStorageError(str(exc)) from exc


def _checked_root(root: object, *, create: bool) -> Path:
    if not isinstance(root, (str, os.PathLike)):
        raise RunStorageError(f"Output folder must be a path, got {root!r}.")
    path = Path(root)
    if path.exists() and not path.is_dir():
        raise RunStorageError(f"The output location {path} exists but is not a folder.")
    if create and not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RunStorageError(f"Could not create the output folder {path}: {exc}") from exc
    return path


def _taken_numbers(root: Path, novel_slug: str) -> set[int]:
    """Run numbers already present for this novel. A hint only; ``mkdir`` is the arbiter."""
    pattern = re.compile(re.escape(novel_slug) + r"-([1-9][0-9]*)", re.IGNORECASE)
    try:
        names = os.listdir(root)
    except OSError:
        return set()
    return {int(m.group(1)) for m in (pattern.fullmatch(n) for n in names) if m}


def _mkdir_exclusive(path: Path) -> None:
    """Create ``path`` or raise ``FileExistsError``; never succeeds on an existing name."""
    os.mkdir(path)


def preview_next_run_dir(root: str | os.PathLike[str], novel_slug: str) -> Path:
    """The folder a new run would get right now. Creates and reserves nothing: another run
    may take it first, and :func:`allocate_run_dir` never trusts a preview."""
    slug = _checked_slug(novel_slug)
    base = _checked_root(root, create=False)
    taken = _taken_numbers(base, slug)
    for number in range(1, MAX_RUN_NUMBER + 1):
        candidate = base / f"{slug}-{number}"
        if number not in taken and not os.path.lexists(candidate):
            return candidate
    raise RunStorageError(f"No free run number left for {slug!r} in {base}.")


def allocate_run_dir(root: str | os.PathLike[str], novel_slug: str) -> RunDirectory:
    """Reserve and return a fresh ``<root>/<novel_slug>-N`` folder (first free N >= 1).

    Raises :class:`RunStorageError` for an invalid slug or root, an exhausted number space,
    or a filesystem failure. A failure after the folder was created removes it again only if
    it is still empty; anything else is left alone and is never reused by a later run.
    """
    slug = _checked_slug(novel_slug)
    base = _checked_root(root, create=True)
    taken = _taken_numbers(base, slug)
    for number in range(1, MAX_RUN_NUMBER + 1):
        if number in taken:
            continue
        candidate = base / f"{slug}-{number}"
        try:
            _mkdir_exclusive(candidate)
        except FileExistsError:
            continue  # lost a race, or a hidden/odd entry: try the next number
        except OSError as exc:
            raise RunStorageError(f"Could not create the run folder {candidate}: {exc}") from exc
        return _write_reservation(candidate, slug, number)
    raise RunStorageError(f"No free run number left for {slug!r} in {base}.")


def _write_reservation(folder: Path, slug: str, number: int) -> RunDirectory:
    payload = {
        "schema_version": RESERVATION_SCHEMA,
        "kind": RESERVATION_KIND,
        "novel_slug": slug,
        "number": number,
        "created_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    try:
        write_json_atomic(folder / RESERVATION_FILENAME, payload)
    except Exception as exc:
        try:
            os.rmdir(folder)  # non-recursive: only an empty folder we just made
            leftover = ""
        except OSError:
            leftover = f" (the partly written folder {folder} was left in place and will not be reused)"
        raise RunStorageError(f"Could not reserve the run folder {folder}: {exc}{leftover}") from exc
    return RunDirectory(path=folder, novel_slug=slug, number=number)


def _read_reservation(folder: Path, slug: str, number: int) -> None:
    marker = folder / RESERVATION_FILENAME
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RunStorageError(
            f"{folder} has no run reservation marker, so this version of the app did not "
            "create it and it cannot be resumed."
        ) from None
    except (OSError, ValueError) as exc:
        raise RunStorageError(f"The run reservation marker in {folder} is unreadable: {exc}") from exc

    def valid() -> bool:
        return (
            isinstance(raw, dict)
            and raw.get("schema_version") == RESERVATION_SCHEMA
            and type(raw.get("schema_version")) is int
            and raw.get("kind") == RESERVATION_KIND
            and raw.get("novel_slug") == slug
            and raw.get("number") == number
            and type(raw.get("number")) is int
            and isinstance(raw.get("created_utc"), str)
            and bool(raw["created_utc"])
        )

    if not valid():
        raise RunStorageError(
            f"The run reservation marker in {folder} is damaged or does not match the folder name."
        )


def open_existing_run(
    root: str | os.PathLike[str],
    run_path: str | os.PathLike[str],
    *,
    expected_novel_slug: Optional[str] = None,
) -> RunDirectory:
    """Validate an earlier run folder for a future Resume and return it. Read-only.

    The folder must be a real (non-link) directory directly inside ``root``, be named
    ``<slug>-N``, and carry a matching reservation marker. Whether its contents are
    resumable (journal, outputs) is the later Resume step's separate check.
    """
    base = Path(root)
    path = Path(run_path)
    if ".." in path.parts or os.path.normcase(os.path.abspath(path.parent)) != os.path.normcase(
        os.path.abspath(base)
    ):
        raise RunStorageError(f"{path} is not directly inside the output folder {base}.")
    match = _RUN_NAME_RE.fullmatch(path.name)
    if match is None or match.group("slug") != match.group("slug").lower():
        raise RunStorageError(f"{path.name!r} is not a run folder name (<novel-slug>-<number>).")
    slug, number = match.group("slug"), int(match.group("number"))
    _checked_slug(slug)
    if not os.path.lexists(path):
        raise RunStorageError(f"The run folder {path} does not exist.")
    if path.is_symlink() or path.is_junction() or not path.is_dir():
        raise RunStorageError(f"{path} is not a folder.")
    for folder in ("PDF", "TXT", "chapter-bodies"):
        checked_output_path(path, folder)
    _read_reservation(path, slug, number)
    if expected_novel_slug is not None and expected_novel_slug != slug:
        raise RunStorageError(
            f"{path.name} belongs to a different novel ({slug!r}, not {expected_novel_slug!r})."
        )
    return RunDirectory(path=path, novel_slug=slug, number=number)
