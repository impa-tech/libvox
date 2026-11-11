"""Linking the books MARC.IA names to their page in the library's OPAC.

**The model never writes a URL.** It names a book in bold, the way the prompt
already asks it to; this module finds that bold title, matches it against the
records the tools actually returned, and builds the address from two values
nobody typed into a chat: the OPAC address the librarian gave the portal, and a
`biblio_id` out of the index. A link can therefore only ever point at the
library's own OPAC, at a record that exists -- not at whatever a prompt
injection, or a model filling in a plausible path, would have produced.

**The shape of the address is the driver's.** `_BUILDERS` is the one place that
maps an `ils_type` to a function, and the Koha one lives in
`/libvox-driver-koha`, because `opac-detail.pl?biblionumber=` is Koha's and the
next ILS spells it differently. A library on an ILS with no builder gets
answers without links, which is what every library had before this.

**A title that does not match gets no link, and that is the failure mode on
purpose.** The match is forgiving where catalogues are predictable -- Koha's
"Dom Casmurro : romance /" against a bold "Dom Casmurro" -- and gives up when two
records would fit, because a link to the wrong book is worse than none.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping

logger = logging.getLogger(__name__)

Builder = Callable[[int], str]
"""biblio_id → the record's page, with the library's OPAC address already
bound in."""


def _koha(opac_url: str) -> Builder:
    from libvox_driver_koha import record_url

    return lambda biblio_id: record_url(opac_url, biblio_id)


_BUILDERS: dict[str, Callable[[str], Builder]] = {"koha": _koha}

_SAFE_SCHEME = re.compile(r"^https?://", re.IGNORECASE)

# The same two bold forms the widget renders as <strong> -- `***x***` first, so
# it is not read as `**` with an asterisk left over. Kept in step with
# EMPHASIS in widget.js: the widget finds a link's place by comparing the
# <strong> text with what is captured here.
_BOLD = re.compile(r"\*\*\*([^*]+)\*\*\*|\*\*([^*]+)\*\*")

_WORD = re.compile(r"\w+")

# A bold "Os" or "IA" is not a title, and would match the start of far too many.
_MIN_CHARS = 4

MAX_SEEN = 200
"""How many records one socket remembers for linking. Enough for many pages of
results; bounded because a socket is open to a stranger for an hour."""


def builder_for(ils_type: str | None, opac_url: str | None) -> Builder | None:
    """The link builder for this library, or None when there cannot be one.

    None without an OPAC address, for an ILS no driver builds links for, and
    for an address that is not http(s) -- the database refuses that already,
    and this is the second lock on a value that ends up in an href.
    """
    if not ils_type or not opac_url or not _SAFE_SCHEME.match(opac_url):
        return None
    make = _BUILDERS.get(ils_type)
    return make(opac_url) if make else None


def remember(seen: dict[int, str], tool_calls: list) -> None:
    """Add the records this answer's searches returned to what the socket saw.

    Across turns, because "is the second one available?" only checks
    availability: the title it names came from the search a turn earlier.
    Oldest forgotten first once there are `MAX_SEEN`.
    """
    records = []
    for call in tool_calls:
        if call.name != "search_catalog" or call.failed:
            continue
        for record in (call.result or {}).get("records") or []:
            biblio_id = record.get("biblio_id")
            title = record.get("title")
            if isinstance(biblio_id, int) and not isinstance(biblio_id, bool) and (
                isinstance(title, str) and title.strip()
            ):
                records.append((biblio_id, title))
    remember_records(seen, records)


def remember_records(seen: dict[int, str], records) -> None:
    """The same, from `(biblio_id, title)` pairs -- a resumed conversation's
    searches, read back from the log."""
    for biblio_id, title in records:
        seen.pop(biblio_id, None)
        seen[biblio_id] = title
    while len(seen) > MAX_SEEN:
        seen.pop(next(iter(seen)))


def record_links(
    text: str, seen: Mapping[int, str], build: Builder
) -> list[dict[str, str]]:
    """`[{"text": <bold text exactly as written>, "href": <record page>}]`.

    One entry per distinct bold text that names exactly one record seen.
    """
    titles = [(biblio_id, _words(title)) for biblio_id, title in seen.items()]
    links: list[dict[str, str]] = []
    done: set[str] = set()

    for match in _BOLD.finditer(text):
        shown = match.group(1) if match.group(1) is not None else match.group(2)
        if shown in done or len(shown.strip()) < _MIN_CHARS:
            continue
        done.add(shown)

        biblio_id = _which(_words(shown), titles)
        if biblio_id is None:
            continue
        try:
            href = build(biblio_id)
        except (TypeError, ValueError):
            logger.warning("could not build a record link")
            continue
        if _SAFE_SCHEME.match(href):
            links.append({"text": shown, "href": href})

    return links


def _words(text: str) -> tuple[str, ...]:
    """Case, accents' case, punctuation and spacing out of the comparison.

    Koha titles end in " /" and split subtitles with " : "; a model writes
    "Título: subtítulo." The words are what the two have in common.
    """
    return tuple(_WORD.findall(text.casefold()))


def _which(
    shown: tuple[str, ...], titles: list[tuple[int, tuple[str, ...]]]
) -> int | None:
    """The one record this bold text names, or None.

    Equal words win outright. Otherwise the bold text may be the start of a
    title (the model dropped a subtitle), or a title of at least two words may
    be the start of the bold text (the model added something after it). Two
    candidates is ambiguity, and ambiguity gets no link.
    """
    if not shown:
        return None

    exact = {biblio_id for biblio_id, words in titles if words == shown}
    if len(exact) == 1:
        return exact.pop()
    if exact:
        return None

    near = {
        biblio_id
        for biblio_id, words in titles
        if words
        and (
            words[: len(shown)] == shown
            or (len(words) >= 2 and shown[: len(words)] == words)
        )
    }
    return near.pop() if len(near) == 1 else None
