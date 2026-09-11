"""Recognize a server-side compare-and-swap failure without exposing Git output."""
import re


def ref_update_conflict(detail: str, ref: str) -> bool:
    # A generic lock error can mean permissions, storage, or a stale lockfile.
    # Only differing object IDs for the exact requested ref establish a race.
    match = re.search(
        r"cannot lock ref '" + re.escape(ref) + r"': is at ([0-9a-f]{40}|[0-9a-f]{64})"
        r" but expected ([0-9a-f]{40}|[0-9a-f]{64})(?![0-9a-f])",
        detail,
    )
    return bool(match and len(match[1]) == len(match[2]) and match[1] != match[2])
