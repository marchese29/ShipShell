"""Tests for tab completion."""

from __future__ import annotations

from shell import completion, rl
from shell.completion import _in_string_literal


class TestInStringLiteral:
    """Unit tests for string literal detection."""

    def test_not_in_string(self):
        assert not _in_string_literal('cd(', 3)

    def test_inside_single_quote(self):
        assert _in_string_literal("cd('/tmp", 4)

    def test_inside_double_quote(self):
        assert _in_string_literal('cd("/tmp', 4)

    def test_after_closing_quote(self):
        assert not _in_string_literal("cd('/tmp')", 10)

    def test_escaped_quote_not_closing(self):
        assert _in_string_literal(r"cd('it\'s", 5)

    def test_empty_line(self):
        assert not _in_string_literal('', 0)

    def test_nested_quotes(self):
        """Double quotes inside single quotes don't close the string."""
        assert _in_string_literal("""cd('"hello""", 5)

    def test_multiple_strings(self):
        """After a closed string and inside a new one."""
        assert _in_string_literal("a('x', '/tmp", 8)

    def test_triple_quote_inside(self):
        """Inside a triple-quoted string."""
        assert _in_string_literal('x = """hello /tmp/', 18)

    def test_triple_quote_after_close(self):
        """After a closed triple-quoted string."""
        assert not _in_string_literal('x = """hello""" + cd(', 21)

    def test_empty_string_then_regular(self):
        """Empty string '' followed by a new string doesn't confuse scanner."""
        assert _in_string_literal("x = '' + cd('/tmp", 14)

    def test_pos_zero(self):
        """Position 0 is never inside a string."""
        assert not _in_string_literal("'hello'", 0)

    def test_at_opening_quote(self):
        """Position right after opening quote is inside the string."""
        assert _in_string_literal("cd('", 4)

    def test_triple_single_quote(self):
        """Triple single quotes work the same as triple double quotes."""
        assert _in_string_literal("x = '''hello /tmp/", 18)

    def test_triple_single_quote_closed(self):
        assert not _in_string_literal("x = '''hello''' + cd(", 21)


class TestCompletionConfig:
    """Test readline completion configuration."""

    def test_set_get_delims_roundtrip(self):
        rl.set_completer_delims(' \t\n')
        assert rl.get_completer_delims() == ' \t\n'

    def test_get_line_buffer_initially_empty(self):
        assert rl.get_line_buffer() == ''

    def test_setup_no_crash(self):
        """completion.setup() configures readline without error."""
        completion.setup()

    def test_setup_sets_delims(self):
        """setup() configures word break characters including quotes."""
        completion.setup()
        delims = rl.get_completer_delims()
        assert "'" in delims
        assert '"' in delims
