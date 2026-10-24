"""UTF-8 plain-text output (v0.3.0 Checkpoint 05).

The same canonical ``ChapterContent`` the PDF renders, in the same order: each chapter is its one
heading line, a blank line, then its paragraphs separated by blank lines; chapters are separated by
two blank lines. UTF-8 without a byte-order mark, ``\\n`` line endings (Notepad on Windows 10 and
later reads both). Every character is kept exactly. Published atomically like the PDF: a temp file
in the destination folder, flushed to disk, then ``os.replace``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from .models import ChapterContent

CHAPTER_SEPARATOR = "\n\n\n"


def chapters_to_txt(chapters: list[ChapterContent]) -> str:
    blocks = ["\n\n".join([chapter.heading, *chapter.paragraphs]) for chapter in chapters]
    return CHAPTER_SEPARATOR.join(blocks) + "\n"


def create_txt(chapters: list[ChapterContent], output_path: Path) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    data = chapters_to_txt(chapters).encode("utf-8")
    fd, tmp_name = tempfile.mkstemp(dir=str(output_path.parent), prefix=".wns-output-",
                                    suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, output_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return output_path
