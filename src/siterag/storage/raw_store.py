"""Raw downloaded files: ``<site_data_dir>/raw/<url_id>.html|.pdf``, bytes as received."""

from __future__ import annotations

import os
from pathlib import Path

from siterag.domain.enums import ContentType

EXTENSIONS = {ContentType.HTML: ".html", ContentType.PDF: ".pdf"}


class RawStore:
    """Latest raw version of each page, uncompressed. One file per page."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def path(self, url_id: str, content_type: ContentType) -> Path:
        """Path of a page's raw file."""
        return self.root / f"{url_id}{EXTENSIONS[content_type]}"

    def exists(self, url_id: str, content_type: ContentType | None) -> bool:
        """True when the page's raw file of that type is on disk."""
        return content_type in EXTENSIONS and self.path(url_id, content_type).is_file()

    def read(self, url_id: str, content_type: ContentType) -> bytes:
        """Return the stored bytes."""
        return self.path(url_id, content_type).read_bytes()

    def save(self, url_id: str, content_type: ContentType, content: bytes) -> Path:
        """Write the bytes atomically and delete a file of another type for the same page."""
        self.root.mkdir(parents=True, exist_ok=True)
        target = self.path(url_id, content_type)
        tmp = target.with_suffix(target.suffix + ".tmp")
        tmp.write_bytes(content)
        os.replace(tmp, target)
        for other_type in EXTENSIONS:
            if other_type != content_type:
                self.path(url_id, other_type).unlink(missing_ok=True)
        return target
