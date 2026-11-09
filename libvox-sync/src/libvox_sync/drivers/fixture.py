"""A driver backed by a captured Koha response, for tests and dry runs.

It reuses the Koha driver's own `normalize_page` rather than reimplementing it,
so a test that passes here exercises the real normalization against real
payloads. A fixture driver with its own parsing would prove nothing.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from libvox_driver_koha import Page, normalize_page


class FixtureDriver:
    """Serves records from a JSON file, paginated like the real thing."""

    name = "fixture"

    def __init__(self, path: str | Path, *, page_size: int = 100) -> None:
        self._rows: list[dict[str, Any]] = json.loads(
            Path(path).read_text(encoding="utf-8")
        )
        if not isinstance(self._rows, list):
            # ValueError, not the TypeError ruff suggests: `path` was a
            # perfectly good path and the file it named holds the wrong thing.
            # The type of what was passed in is not what is wrong here.
            raise ValueError(  # noqa: TRY004
                f"{path} does not contain a JSON array of records"
            )
        self._page_size = page_size

    def __len__(self) -> int:
        return len(self._rows)

    def fetch_page(self, cursor: str | None = None) -> Page:
        offset = int(cursor) if cursor else 0
        rows = self._rows[offset : offset + self._page_size]
        records, skipped = normalize_page(rows)

        # Mirrors Koha's contract exactly, including the extra empty request
        # after an exactly-full final page — so pipeline code that works here
        # works against the live ILS.
        next_offset = offset + self._page_size
        next_cursor = str(next_offset) if len(rows) == self._page_size else None

        return Page(records=records, next_cursor=next_cursor, skipped=skipped)
