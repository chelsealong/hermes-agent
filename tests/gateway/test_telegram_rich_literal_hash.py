"""Tests for literal hash-reference escaping in rich messages (issue #105483).

Bot API 10.1 ``sendRichMessage`` promotes a bare leading ``#`` run to a heading
even when it has no trailing space, unlike CommonMark ATX headings (which
require one). Literal references such as ``#89`` or ``- #181 ...`` hit this
and lose their ``#`` to heading styling.

``_rich_message_payload`` must escape that non-conforming leading ``#`` so it
stays literal text, while leaving genuine headings, code, and other rich
constructs untouched.

The ``telegram`` package is mocked by ``tests/gateway/conftest.py``, so these
tests construct a real ``TelegramAdapter``.
"""

import pytest

from plugins.platforms.telegram.adapter import TelegramAdapter


@pytest.fixture()
def adapter():
    """Bare adapter instance — _rich_message_payload doesn't use self."""
    return object.__new__(TelegramAdapter)


class TestLiteralHashEscaping:
    def test_bare_leading_hash_reference_escaped(self, adapter):
        """A literal ``#89`` with no space after the hash must not become a heading."""
        md = adapter._rich_message_payload("#89 remains open.")["markdown"]
        assert md == "\\#89 remains open."

    def test_list_item_hash_reference_escaped(self, adapter):
        """A literal reference inside a list item must also stay literal."""
        content = "- #181 reported a formatting problem."
        md = adapter._rich_message_payload(content)["markdown"]
        assert md == "- \\#181 reported a formatting problem."

    def test_genuine_heading_untouched(self, adapter):
        """A real ATX heading (space after the hashes) must keep its heading marker."""
        md = adapter._rich_message_payload("## Real heading")["markdown"]
        assert md == "## Real heading"

    def test_mixed_content(self, adapter):
        content = "#89 remains open.\n\n- #181 reported a formatting problem.\n\n## Real heading"
        md = adapter._rich_message_payload(content)["markdown"]
        assert "\\#89 remains open." in md
        assert "- \\#181 reported a formatting problem." in md
        assert "## Real heading" in md

    def test_hash_inside_fenced_code_untouched(self, adapter):
        """A ``#`` at line start inside a fenced code block (e.g. a shebang or comment) is not markdown."""
        content = "```\n#!/usr/bin/env python\n# a comment\n```"
        md = adapter._rich_message_payload(content)["markdown"]
        assert md == content

    def test_hash_inside_table_untouched(self, adapter):
        content = "| Issue | # |\n|---|---|\n| #89 | 1 |"
        md = adapter._rich_message_payload(content)["markdown"]
        assert md == content
