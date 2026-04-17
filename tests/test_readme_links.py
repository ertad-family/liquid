"""Verify all relative file links in README.md exist."""

import re
from pathlib import Path

README = Path(__file__).parent.parent / "README.md"


def test_readme_local_links_exist():
    """All relative links in README resolve to existing files."""
    if not README.exists():
        return  # No README yet
    text = README.read_text()
    # Match [text](path) where path doesn't start with http or #
    pattern = r"\[[^\]]+\]\(((?!http|#|mailto:)[^)\s]+)\)"
    links = re.findall(pattern, text)
    missing = []
    for link in links:
        # Strip anchor
        path = link.split("#")[0]
        if not path:
            continue
        resolved = (README.parent / path).resolve()
        if not resolved.exists():
            missing.append(link)
    assert not missing, f"Broken README links: {missing}"
