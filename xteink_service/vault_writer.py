import json
import logging
import re
from datetime import date, datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


def _sanitize(name: str) -> str:
    """Remove characters unsafe for filesystem paths from a book/file name."""
    return "".join(c for c in name if c.isalnum() or c in " .-_").strip()


def _parse_cfi(progress: str | None) -> str | None:
    """Extract a human-readable position indicator from a KOReader progress string."""
    if not progress:
        return None
    m = re.search(r'DocFragment\[(\d+)\]', progress)
    if m:
        return f"§{m.group(1)}"
    stripped = progress.strip()
    if stripped.lstrip('-').isdigit():
        return f"p.{stripped}"
    return None


class VaultWriter:
    """Writes screenshots and reading diary entries to the Obsidian vault."""

    def __init__(self, vault_path: str):
        self.vault_path = Path(vault_path)

    # ------------------------------------------------------------------ #
    # Screenshots                                                          #
    # ------------------------------------------------------------------ #

    def write_screenshot(self, book_title: str, day: date, png_data: bytes, index: int) -> str:
        """
        Write PNG bytes to Books/<book>/<date>-NN.png.
        Returns the embed path used in the book note (e.g. 'Pastoral/2026-07-04-01.png').
        """
        date_str = day.strftime("%Y-%m-%d")
        filename = f"{date_str}-{index:02d}.png"
        book_slug = _sanitize(book_title)
        book_dir = self.vault_path / "Books" / book_slug
        book_dir.mkdir(parents=True, exist_ok=True)
        (book_dir / filename).write_bytes(png_data)
        logger.debug("Wrote %s", book_dir / filename)
        return f"{book_slug}/{filename}"

    def write_screenshot_meta(
        self,
        embed_path: str,
        device_path: str,
        content_hash: str,
        book_title: str,
        sync_date: str,
        ocr_text: str | None,
    ) -> None:
        """
        Write a JSON sidecar alongside the PNG.
        Provides a DB-independent backup: the vault folder is self-contained.
        """
        png_path = self.vault_path / "Books" / embed_path
        meta = {
            "device_path": device_path,
            "content_hash": content_hash,
            "book_title": book_title,
            "sync_date": sync_date,
            "ocr_text": ocr_text,
            "archived_at": datetime.now(timezone.utc).isoformat(),
        }
        json_path = png_path.with_suffix(".json")
        json_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False))
        logger.debug("Wrote sidecar %s", json_path)

    def append_to_daily_note(
        self,
        book_title: str,
        day: date,
        embed_path: str,
        ocr_text: str | None = None,
    ) -> None:
        """
        Append a screenshot embed (and optional collapsible OCR callout) to
        Books/<book>.md under a ## YYYY-MM-DD date heading.
        Creates the note with frontmatter if it doesn't exist.
        """
        date_str = day.strftime("%Y-%m-%d")
        book_slug = _sanitize(book_title)
        note_path = self.vault_path / "Books" / f"{book_slug}.md"

        if not note_path.exists():
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(f'---\ntitle: "{book_title}"\n---\n')

        content = note_path.read_text()
        date_heading = f"## {date_str}"

        with note_path.open("a") as f:
            if date_heading not in content:
                f.write(f"\n{date_heading}\n\n")
            f.write(f"![[{embed_path}]]\n")
            if ocr_text:
                body = "\n".join(
                    f"> {line}" if line.strip() else ">"
                    for line in ocr_text.splitlines()
                )
                f.write(f"> [!quote] OCR text\n{body}\n\n")
            else:
                f.write("\n")

    # ------------------------------------------------------------------ #
    # Reading log / book timeline (Phase 7)                               #
    # ------------------------------------------------------------------ #

    def _write_persistent_log(self, day: date, title: str, new_line: str) -> None:
        """
        Write/update a reading entry in the all-time Reading Log.md.
        Newest date headings are inserted at the top; one line per book per day,
        replaced in-place on subsequent syncs.
        """
        log_path = self.vault_path / "Reading Log.md"
        date_heading = f"## {day.strftime('%Y-%m-%d')}"

        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"# Reading Log\n\n{date_heading}\n\n{new_line}")
            return

        content = log_path.read_text()
        lines = content.splitlines(keepends=True)

        if date_heading + "\n" in content or date_heading + "\r" in content or (date_heading in content):
            # Date exists — replace the entry for this title under it
            new_lines, in_section, replaced = [], False, False
            for ln in lines:
                if ln.strip() == date_heading:
                    in_section = True
                    new_lines.append(ln)
                elif in_section and ln.startswith("## "):
                    if not replaced:
                        new_lines.append(new_line)
                        replaced = True
                    in_section = False
                    new_lines.append(ln)
                elif in_section and ln.startswith(f"- **{title}**") and not replaced:
                    new_lines.append(new_line)
                    replaced = True
                else:
                    new_lines.append(ln)
            if in_section and not replaced:
                new_lines.append(new_line)
            log_path.write_text("".join(new_lines))
        else:
            # New date — insert it right after the "# Reading Log" title line
            new_lines = []
            inserted = False
            for ln in lines:
                new_lines.append(ln)
                if not inserted and ln.startswith("# Reading Log"):
                    new_lines.append("\n")
                    new_lines.append(f"{date_heading}\n")
                    new_lines.append("\n")
                    new_lines.append(new_line)
                    inserted = True
            if not inserted:
                new_lines.append(f"\n{date_heading}\n\n{new_line}")
            log_path.write_text("".join(new_lines))

    def write_reading_log(
        self,
        day: date,
        title: str,
        percentage: float,
        page: int | None = None,
        total_pages: int | None = None,
        progress: str | None = None,
        prev_percentage: float | None = None,
        prev_day: date | None = None,
    ) -> None:
        date_str = day.strftime("%Y-%m-%d")
        log_path = self.vault_path / "Reading Log" / f"{date_str}.md"
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"# Reading Log \u2014 {date_str}\n\n")

        loc = _parse_cfi(progress)

        if prev_percentage is not None and prev_day is not None and prev_day < day:
            body = f"{prev_percentage:.1f}% \u2192 {percentage:.1f}%"
        else:
            body = f"{percentage:.1f}%"
        if loc:
            body += f"  [{loc}]"
        new_line = f"- **{title}** \u2014 {body}\n"

        content = log_path.read_text()
        lines = content.splitlines(keepends=True)
        new_lines, replaced = [], False
        for ln in lines:
            if ln.startswith(f"- **{title}**"):
                new_lines.append(new_line)
                replaced = True
            else:
                new_lines.append(ln)
        if not replaced:
            new_lines.append(new_line)
        log_path.write_text("".join(new_lines))
        self._write_persistent_log(day, title, new_line)

    def update_book_timeline(
        self,
        title: str,
        author: str,
        day: date,
        percentage: float,
        page: int | None = None,
        total_pages: int | None = None,
        progress: str | None = None,
        first_today_pct: float | None = None,
    ) -> None:
        date_str = day.strftime("%Y-%m-%d")
        book_slug = _sanitize(title)
        book_path = self.vault_path / "Books" / f"{book_slug}.md"
        date_heading = f"## {date_str}"

        loc = _parse_cfi(progress)
        if first_today_pct is not None:
            body = f"{first_today_pct:.1f}% \u2192 {percentage:.1f}%"
        else:
            body = f"{percentage:.1f}%"
        if loc:
            body += f"  [{loc}]"
        new_line = f"- {body}\n"

        if not book_path.exists():
            book_path.parent.mkdir(parents=True, exist_ok=True)
            book_path.write_text(
                f'---\ntitle: "{title}"\nauthor: "{author or "Unknown"}"\n'
                f'status: "Reading"\nfirst_opened: {date_str}\n---\n'
            )

        content = book_path.read_text()
        if date_heading in content:
            # Replace existing progress line under today's heading
            lines = content.splitlines(keepends=True)
            new_lines, in_section, replaced = [], False, False
            for ln in lines:
                if ln.strip() == date_heading:
                    in_section = True
                    new_lines.append(ln)
                elif in_section and ln.startswith("## "):
                    if not replaced:
                        new_lines.append(new_line)
                        replaced = True
                    in_section = False
                    new_lines.append(ln)
                elif in_section and re.match(r'^- [\d]', ln) and not replaced:
                    new_lines.append(new_line)
                    replaced = True
                else:
                    new_lines.append(ln)
            if in_section and not replaced:
                new_lines.append(new_line)
            book_path.write_text("".join(new_lines))
        else:
            with book_path.open("a") as f:
                f.write(f"\n{date_heading}\n\n{new_line}")
