"""The two Troubleshooting entries about a lost backend point at each other.

``"Lost connection to PlexMix" or "That playlist didn't finish"`` (the Generator's lost-connection
notice) and ``"Cannot connect to server", or a command that fails with Local embeddings on macOS``
(the Local embedding crash) describe the same symptom from two sides: the page cannot reach
PlexMix. A reader who lands on one must be able to reach the other, so each entry links to the
other (the crash change's README task 5.5 and design D13 ask for both links).

The crash entry arrives with ``fix-local-embedding-crash``. Until it is in the README the back-link
test skips; once it is there, a missing back-link or a link that no longer matches its heading
fails the suite.
"""

import re
from pathlib import Path
from typing import List, Optional

import pytest

README = Path(__file__).resolve().parents[1] / "README.md"

LOST_HEADING_START = '### "Lost connection to PlexMix"'
CRASH_HEADING_START = '### "Cannot connect to server"'

LOST_ANCHOR = "#lost-connection-to-plexmix-or-that-playlist-didnt-finish"
CRASH_ANCHOR = "#cannot-connect-to-server-or-a-command-that-fails-with-local-embeddings-on-macos"


def github_slug(heading_line: str) -> str:
    """Return the anchor GitHub gives a Markdown heading line such as ``### Title``."""
    text = heading_line.lstrip("#").strip().lower()
    return "#" + re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def find_entry(lines: List[str], heading_start: str) -> Optional[List[str]]:
    """Return a Troubleshooting entry: its heading line and the lines up to the next heading.

    A line that starts with ``#`` inside a code fence is a comment, not a heading, so fences are
    tracked.
    """
    in_fence = False
    start: Optional[int] = None
    for index, line in enumerate(lines):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if start is None:
            if line.startswith(heading_start):
                start = index
            continue
        if re.match(r"#{1,3} ", line):
            return lines[start:index]
    return None if start is None else lines[start:]


@pytest.fixture(scope="module")
def readme_lines() -> List[str]:
    return README.read_text(encoding="utf-8").splitlines()


def test_lost_connection_entry_anchor_matches_its_heading(readme_lines: List[str]) -> None:
    entry = find_entry(readme_lines, LOST_HEADING_START)
    assert entry is not None, "README.md has no Troubleshooting entry for a lost connection"
    assert github_slug(entry[0]) == LOST_ANCHOR


def test_lost_connection_entry_links_to_the_crash_entry(readme_lines: List[str]) -> None:
    entry = find_entry(readme_lines, LOST_HEADING_START)
    assert entry is not None
    assert f"]({CRASH_ANCHOR})" in "\n".join(entry), (
        "the 'Lost connection' entry must link to the 'Cannot connect to server' entry "
        "(crash change task 5.5)"
    )


def test_crash_entry_heading_matches_the_link_to_it(readme_lines: List[str]) -> None:
    entry = find_entry(readme_lines, CRASH_HEADING_START)
    if entry is None:
        pytest.skip("the 'Cannot connect to server' entry is not in this README yet")
    assert github_slug(entry[0]) == CRASH_ANCHOR


def test_crash_entry_links_back_to_the_lost_connection_entry(readme_lines: List[str]) -> None:
    entry = find_entry(readme_lines, CRASH_HEADING_START)
    if entry is None:
        pytest.skip("the 'Cannot connect to server' entry is not in this README yet")
    assert f"]({LOST_ANCHOR})" in "\n".join(entry), (
        "the 'Cannot connect to server' entry must link back to the 'Lost connection' entry "
        "(crash change task 5.5)"
    )
