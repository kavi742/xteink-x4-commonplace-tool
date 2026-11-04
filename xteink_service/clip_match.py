"""Link a clipping to the archived screenshot that shows it.

Both sides carry the same position, encoded differently: a clipping stores
``spine`` / ``start_page`` / ``end_page`` 0-based, while a screenshot's device
filename stores it 1-based as ``..._ch{N}_p{M}_...``. Neither signal is
trustworthy on its own — page numbers are layout-dependent, so a clipping taken
at a different font size disagrees with the screenshot, and OCR text is lossy —
so a link is only reported when position and text agree, or when one of them is
unambiguous by itself. Anything less returns ``None``: sending the reader to the
wrong page of the book is worse than offering no link at all.

Nothing is persisted. Aliases and OCR text are hand-corrected and stitched-group
ids shift when a book is re-synced, so a stored link would go stale.
"""
import re
import sqlite3

from xteink_service.archiver import parse_screenshot_filename
from xteink_service.booktitle import canonical_book_slug

# A clipping and a screenshot can disagree by a page when they were made at
# different font sizes; screenshots carry no layout_signature to detect it.
_PAGE_TOLERANCE = 1

_PROBE_WORDS = 8
_MIN_PROBE_WORDS = 3

_HYPHEN_BREAK = re.compile(r"-\s*\n\s*")
_NON_WORD = re.compile(r"[^\w]")


def _words(text: str) -> list[str]:
    """Normalise prose into comparable words.

    Rejoins hyphenated line breaks (OCR keeps the printed hyphen), then
    lower-cases and strips punctuation the way ``_find_text_bboxes`` does.
    """
    if not text:
        return []
    out: list[str] = []
    for token in _HYPHEN_BREAK.sub("", text).split():
        word = _NON_WORD.sub("", token).lower()
        if word:
            out.append(word)
    return out


class _BookIndex:
    """One book's screenshots, pre-indexed for repeated clipping lookups."""

    def __init__(self) -> None:
        self._shots: list[dict] = []
        self._words: list[list[str]] = []
        self._postings: dict[str, list[tuple[int, int]]] = {}
        # Stitched pages share one image under several rows; the screenshots
        # API only ever lists the lowest id of each group.
        self._canonical: dict[str, int] = {}

    def add(self, row: sqlite3.Row) -> None:
        key = row["vault_png_path"] or f"id:{row['id']}"
        known = self._canonical.get(key)
        if known is None or row["id"] < known:
            self._canonical[key] = row["id"]

        position = parse_screenshot_filename((row["device_path"] or "").rsplit("/", 1)[-1])
        words = _words(row["ocr_corrected"] or row["ocr_text"] or "")
        index = len(self._shots)
        self._shots.append({
            "key": key,
            "chapter": position.get("chapter"),
            "page": position.get("page"),
        })
        self._words.append(words)
        for offset, word in enumerate(words):
            self._postings.setdefault(word, []).append((index, offset))

    # -------------------------------------------------------------- #

    def _position_matches(self, spine: int, start_page: int, end_page: int) -> set[int]:
        chapter = spine + 1
        low = start_page + 1 - _PAGE_TOLERANCE
        high = end_page + 1 + _PAGE_TOLERANCE
        return {
            i for i, shot in enumerate(self._shots)
            if shot["chapter"] == chapter
            and shot["page"] is not None
            and low <= shot["page"] <= high
        }

    def _runs_of(self, probe: list[str]) -> set[int]:
        span = len(probe)
        return {
            i for i, offset in self._postings.get(probe[0], ())
            if self._words[i][offset:offset + span] == probe
        }

    def _text_matches(self, words: list[str]) -> set[int]:
        if len(words) < _MIN_PROBE_WORDS:
            return set()
        probe = words[:_PROBE_WORDS]
        hits = self._runs_of(probe)
        # OCR mangles a word often enough that the full opening line misses.
        if not hits and len(probe) > _MIN_PROBE_WORDS:
            hits = self._runs_of(probe[:_MIN_PROBE_WORDS])
        return hits

    def _sole_image(self, shots: set[int]) -> int | None:
        ids = {self._canonical[self._shots[i]["key"]] for i in shots}
        return ids.pop() if len(ids) == 1 else None

    def match(self, clip: dict) -> int | None:
        position = self._position_matches(
            clip["spine"], clip["start_page"], clip["end_page"]
        )
        text = self._text_matches(_words(clip.get("text") or ""))

        agreed = position & text
        if agreed:
            wanted = clip["start_page"] + 1
            best = min(agreed, key=lambda i: (abs(self._shots[i]["page"] - wanted), i))
            return self._canonical[self._shots[best]["key"]]

        # One signal alone only counts when it points at a single image.
        sole = self._sole_image(text)
        return sole if sole is not None else self._sole_image(position)


def _load_books(conn: sqlite3.Connection, slugs: set[str]) -> dict[str, _BookIndex]:
    """Index every screenshot belonging to the given book slugs, in two queries.

    ``synced_screenshots`` has no slug column, so the titles are canonicalised
    here the way ``api._book_titles_for_slug`` does.
    """
    titles = [
        r["book_title"]
        for r in conn.execute("SELECT DISTINCT book_title FROM synced_screenshots")
        if r["book_title"] and canonical_book_slug(r["book_title"]) in slugs
    ]
    if not titles:
        return {}

    placeholders = ",".join("?" * len(titles))
    books: dict[str, _BookIndex] = {}
    for row in conn.execute(
        "SELECT id, device_path, book_title, vault_png_path, ocr_text, ocr_corrected "
        f"FROM synced_screenshots WHERE book_title IN ({placeholders}) ORDER BY id",
        titles,
    ):
        slug = canonical_book_slug(row["book_title"])
        books.setdefault(slug, _BookIndex()).add(row)
    return books


def attach_screenshot_ids(
    conn: sqlite3.Connection,
    clips: list[dict],
    slug_by_hash: dict[str, str],
) -> None:
    """Add a ``screenshot_id`` key to every clipping in ``clips``, in place.

    The value is the id the screenshots API serves for the matching image
    (``MIN(id)`` of a stitched group), or ``None`` when the book is unknown or
    no screenshot can be confidently matched.
    """
    for clip in clips:
        clip["screenshot_id"] = None

    slugs = {slug_by_hash.get(clip["document"], "") for clip in clips}
    slugs.discard("")
    if not slugs:
        return

    try:
        books = _load_books(conn, slugs)
    except sqlite3.Error:
        return  # no screenshots archived yet

    for clip in clips:
        book = books.get(slug_by_hash.get(clip["document"], ""))
        if book is not None:
            clip["screenshot_id"] = book.match(clip)
