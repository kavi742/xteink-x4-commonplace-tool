import logging
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)


def _sanitize(name: str) -> str:
    """Remove characters unsafe for filesystem paths from a book/file name."""
    return "".join(c for c in name if c.isalnum() or c in " .-_").strip()


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
                f.write(f"> [!quote]- OCR text\n{body}\n\n")
            else:
                f.write("\n")

    # ------------------------------------------------------------------ #
    # Reading log / book timeline (Phase 7)                               #
    # ------------------------------------------------------------------ #

    def write_reading_log(self, day: date, title: str, page: int, total_pages: int) -> None:
        date_str = day.strftime("%Y-%m-%d")
        log_path = self.vault_path / "Reading Log" / f"{date_str}.md"
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"# Reading Log \u2014 {date_str}\n\n")
        percent = int((page / total_pages) * 100) if total_pages else 0
        with log_path.open("a") as f:
            f.write(f"- **{title}** \u2192 Page {page}/{total_pages} ({percent}%)\n")

    def update_book_timeline(
        self, title: str, author: str, day: date, page: int, total_pages: int
    ) -> None:
        date_str = day.strftime("%Y-%m-%d")
        book_slug = _sanitize(title)
        book_path = self.vault_path / "Books" / f"{book_slug}.md"
        percent = int((page / total_pages) * 100) if total_pages else 0
        entry = f"- Page {page}/{total_pages} ({percent}%)\n"
        date_heading = f"## {date_str}"

        if not book_path.exists():
            book_path.parent.mkdir(parents=True, exist_ok=True)
            book_path.write_text(
                f'---\ntitle: "{title}"\nauthor: "{author or "Unknown"}"\n'
                f'status: "Reading"\nfirst_opened: {date_str}\n---\n'
            )

        content = book_path.read_text()
        with book_path.open("a") as f:
            if date_heading not in content:
                f.write(f"\n{date_heading}\n\n")
            f.write(entry)
