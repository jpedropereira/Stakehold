"""The issuer a constituent line belongs to, for single-name overlap (risk spec §8).

Funds list share classes as separate lines (IWDA: "ALPHABET CLASS A" and "ALPHABET CLASS C"),
and no source gives an issuer id. The key is the name with its trailing share-class words
stripped, plus the line's location. The location keeps apart different companies that share a
name: IWDA holds an "EQT" in the US and another in Sweden.

**A heuristic, with a known failure.** Two different companies with the same name in the same
country would merge, and a share class named in a way not stripped here stays separate. Neither
occurs in IWDA's 2026-08-30 snapshot. The exact fix is an issuer id from the data source.
"""

import re

# Repeated, from the end: "SUBORDINATE VOTING CLASS A" strips both.
_SUFFIX = re.compile(
    r"\s+(?:CLASS\s+[A-Z]|SUBORDINATE\s+VOTING|NON[\s-]?VOTING|PARTICIPATION\s+CERTIFICATE"
    r"|PREF(?:ERRED)?|REG(?:ISTERED)?)$"
)


def issuer_key(name: str | None, location: str | None) -> tuple[str, str | None] | None:
    """(normalised name, location), or None for a line with no name."""
    if name is None:
        return None
    normalised = " ".join(name.upper().split())
    if not normalised:
        return None
    while True:
        stripped = _SUFFIX.sub("", normalised)
        if stripped == normalised or not stripped:
            break
        normalised = stripped
    return normalised, location
