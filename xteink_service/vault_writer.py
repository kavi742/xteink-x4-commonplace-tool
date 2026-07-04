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
        Write PNG bytes to Commonplace/<book>/attachments/YYYY-MM-DD-NN.png.
        Returns the relative embed path (e.g. 'attachments/2026-07-03-01.png').
        """
        date_str = day.strftime("%Y-%m-%d")
        filename = f"{date_str}-{index:02d}.png"
        book_dir = self.vault_path / "Commonplace" / _sanitize(book_title)
        attachments_dir = book_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)
        (attachments_dir / filename).write_bytes(png_data)
        logger.debug("Wrote %s", attachments_dir / filename)
        return f"attachments/{filename}"

    def append_to_daily_note(
        self,
        book_title: str,
        day: date,
        embed_path: str,
        ocr_text: str | None = None,
    ) -> None:
        """
        Append a screenshot embed (and optional collapsible OCR callout) to
        Commonplace/<book>/YYYY-MM-DD.md, creating the note if it doesn't exist.
        """
        date_str = day.strftime("%Y-%m-%d")
        book_dir = self.vault_path / "Commonplace" / _sanitize(book_title)
        note_path = book_dir / f"{date_str}.md"

        if not note_path.exists():
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(f"# {date_str} \u2014 {book_title}\n\n")

        with note_path.open("a") as f:
            f.write(f"![[{embed_path}]]\n")
            if ocr_text:
                # Collapsible callout — collapsed by default (-), indexed by Obsidian search
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
        book_path = self.vault_path / "Books" / f"{_sanitize(title)}.md"
        percent = int((page / total_pages) * 100) if total_pages else 0
        entry = f"- **{date_str}**: {percent}% (Page {page}/{total_pages})\n"
        if book_path.exists():
            with book_path.open("a") as f:
                f.write(entry)
        else:
            book_path.parent.mkdir(parents=True, exist_ok=True)
            book_path.write_text(
                f'---\ntitle: "{title}"\nauthor: "{author or "Unknown"}"\n'
                f'status: "Reading"\nfirst_opened: {date_str}\n---\n\n'
                f"## Reading Timeline\n{entry}"
            )
