"""URLs for the files under /static and /brand, versioned by their content.

StaticFiles sends no Cache-Control, so a browser judges a cached file's freshness on its own
and may reuse it for days without asking. With a fixed URL, a changed script stays stale: a
cached chart_theme.js without `formatPct` once left the Labs drawdown chart blank. `?v=` is a
hash of the file's content, so a changed file gets a new URL that no cached copy matches, and
an unchanged one keeps its URL, and its cache. StaticFiles ignores the query string.

The hash is recomputed only when the file's mtime or size changes, so a render costs a stat
per asset, and an edited file (brand/ is mounted, not baked) gets a new URL without a restart.
"""

import hashlib
from collections.abc import Callable, Mapping
from pathlib import Path

_VERSION_LENGTH = 12


class UnknownAsset(ValueError):
    """A template named a file that isn't under a mount. Raised, not ignored: an unversioned
    or broken link would fail quietly in the browser instead."""


def make_asset_url(mounts: Mapping[str, Path]) -> Callable[[str], str]:
    """`asset_url("/static/labs_chart.js")` → `/static/labs_chart.js?v=<hash>`. `mounts` maps
    each URL prefix to the directory it serves, as app.py mounts them."""
    roots = {
        prefix.rstrip("/") + "/": Path(directory).resolve() for prefix, directory in mounts.items()
    }
    versions: dict[Path, tuple[tuple[int, int], str]] = {}

    def file_for(url_path: str) -> Path:
        for prefix, root in roots.items():
            if url_path.startswith(prefix):
                file = (root / url_path[len(prefix) :]).resolve()
                if file.is_relative_to(root) and file.is_file():
                    return file
        raise UnknownAsset(f"no asset at {url_path!r}")

    def asset_url(url_path: str) -> str:
        file = file_for(url_path)
        stat = file.stat()
        key = (stat.st_mtime_ns, stat.st_size)
        cached = versions.get(file)
        if cached is None or cached[0] != key:
            digest = hashlib.sha256(file.read_bytes()).hexdigest()[:_VERSION_LENGTH]
            cached = versions[file] = (key, digest)
        return f"{url_path}?v={cached[1]}"

    return asset_url
