"""Canonical book-identity slug shared by the screenshot and reading-log sides.

A book is keyed by two different free-text strings depending on where it comes
from:

* Screenshots use the on-device ``/screenshots/<folder>`` name, which hyphenates
  spaces — e.g. ``"The-Hidden-Keys"``.
* KOReader progress is resolved through ``document_aliases.title``, which is the
  Calibre library form — e.g. ``"Hidden Keys, The - Andre Alexis"``.

Because those strings never match, the same book used to split into two entries
(and its screenshots and reading log landed on two different URLs).
``canonical_book_slug`` maps both forms to one value so the API can join them
without the user hand-editing every alias.
"""
import re

_ARTICLE_SUFFIX = re.compile(r"^(.*),\s*(the|a|an)$", re.IGNORECASE)


def canonical_book_slug(title: str | None) -> str:
    """Return a normalised, comparable slug for a book title.

    Examples (all collapse to ``"the-hidden-keys"``)::

        "The-Hidden-Keys"
        "Hidden Keys, The - Andre Alexis"
        "the hidden keys"

    The transform is:

    1. Strip a trailing ``" - Author"`` segment. A spaced ``" - "`` only occurs
       in Calibre-style alias titles (screenshot folder names hyphenate spaces),
       so this never eats a screenshot slug.
    2. Move a trailing ``", The"`` / ``", A"`` / ``", An"`` article to the front.
    3. Lower-case and replace every run of non-alphanumeric characters with a
       single hyphen.
    """
    if not title:
        return ""
    s = title.strip()

    # 1. Drop a trailing " - Author" segment (keep earlier " - " in the title).
    if " - " in s:
        head = s.rsplit(" - ", 1)[0].strip()
        if head:
            s = head

    # 2. "Hidden Keys, The" -> "The Hidden Keys"
    m = _ARTICLE_SUFFIX.match(s)
    if m:
        s = f"{m.group(2)} {m.group(1)}"

    # 3. Slugify.
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
