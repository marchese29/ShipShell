"""Unit tests for pure functions in the bash compatibility layer.

These functions have no side effects and can be tested directly without
fork-based isolation.
"""


from shell.compat.bash import (
    _bash_to_file,
    _bash_to_int,
    _bash_to_str,
    _expand_braces,
    _expand_range,
    _split_commas,
)

# === _bash_to_str tests ===


class TestBashToStr:
    """Test _bash_to_str conversion."""

    def test_string_passthrough(self):
        assert _bash_to_str('hello') == 'hello'
        assert _bash_to_str('') == ''

    def test_int_to_str(self):
        assert _bash_to_str(42) == '42'
        assert _bash_to_str(0) == '0'
        assert _bash_to_str(-1) == '-1'

    def test_list_first_element(self):
        assert _bash_to_str(['a', 'b', 'c']) == 'a'
        assert _bash_to_str(['only']) == 'only'

    def test_empty_list(self):
        assert _bash_to_str([]) == ''

    def test_none(self):
        assert _bash_to_str(None) == ''


# === _bash_to_file tests ===


class TestBashToFile:
    """Test _bash_to_file conversion."""

    def test_int_fd(self):
        assert _bash_to_file(1) == 1
        assert _bash_to_file(2) == 2

    def test_string_path(self):
        assert _bash_to_file('/tmp/file') == '/tmp/file'
        assert _bash_to_file('output.txt') == 'output.txt'

    def test_list_first_element(self):
        assert _bash_to_file(['/a', '/b']) == '/a'

    def test_empty_list(self):
        assert _bash_to_file([]) == ''

    def test_none(self):
        assert _bash_to_file(None) == ''


# === _bash_to_int tests ===


class TestBashToInt:
    """Test _bash_to_int conversion."""

    def test_int_passthrough(self):
        assert _bash_to_int(42) == 42
        assert _bash_to_int(0) == 0
        assert _bash_to_int(-5) == -5

    def test_string_to_int(self):
        assert _bash_to_int('42') == 42
        assert _bash_to_int('0') == 0
        assert _bash_to_int('-5') == -5

    def test_invalid_string(self):
        assert _bash_to_int('hello') == 0
        assert _bash_to_int('') == 0
        assert _bash_to_int('12.5') == 0

    def test_list_returns_zero(self):
        assert _bash_to_int(['1', '2']) == 0
        assert _bash_to_int([]) == 0

    def test_none(self):
        assert _bash_to_int(None) == 0


# === _expand_range tests ===


class TestExpandRange:
    """Test _expand_range for bash range expansion."""

    def test_no_range(self):
        assert _expand_range('abc') is None
        assert _expand_range('1,2,3') is None

    def test_numeric_ascending(self):
        assert _expand_range('1..5') == ['1', '2', '3', '4', '5']
        assert _expand_range('0..3') == ['0', '1', '2', '3']

    def test_numeric_descending(self):
        assert _expand_range('5..1') == ['5', '4', '3', '2', '1']
        assert _expand_range('3..0') == ['3', '2', '1', '0']

    def test_with_increment(self):
        assert _expand_range('1..10..2') == ['1', '3', '5', '7', '9']
        assert _expand_range('0..10..3') == ['0', '3', '6', '9']

    def test_descending_with_increment(self):
        assert _expand_range('10..1..2') == ['10', '8', '6', '4', '2']

    def test_zero_increment(self):
        assert _expand_range('1..5..0') is None

    def test_zero_padded(self):
        assert _expand_range('01..05') == ['01', '02', '03', '04', '05']
        assert _expand_range('001..003') == ['001', '002', '003']

    def test_invalid_range(self):
        assert _expand_range('a..z') is None  # Letters not supported yet
        assert _expand_range('1..2..3..4') is None  # Too many parts


# === _split_commas tests ===


class TestSplitCommas:
    """Test _split_commas for splitting at depth 0."""

    def test_simple_split(self):
        assert _split_commas('a,b,c') == ['a', 'b', 'c']
        assert _split_commas('one,two') == ['one', 'two']

    def test_no_commas(self):
        assert _split_commas('hello') == ['hello']
        assert _split_commas('') == []

    def test_nested_braces(self):
        # Commas inside braces should not split
        assert _split_commas('a,{b,c},d') == ['a', '{b,c}', 'd']
        assert _split_commas('{a,b},{c,d}') == ['{a,b}', '{c,d}']

    def test_deeply_nested(self):
        assert _split_commas('a,{b,{c,d}},e') == ['a', '{b,{c,d}}', 'e']

    def test_escaped_comma(self):
        assert _split_commas('a\\,b,c') == ['a\\,b', 'c']

    def test_escaped_braces(self):
        assert _split_commas('a,\\{b,c\\}') == ['a', '\\{b', 'c\\}']


# === _expand_braces tests ===


class TestExpandBraces:
    """Test _expand_braces for full brace expansion."""

    def test_simple_expansion(self):
        assert _expand_braces('{a,b,c}') == ['a', 'b', 'c']
        assert _expand_braces('{one,two}') == ['one', 'two']

    def test_with_prefix(self):
        assert _expand_braces('pre{a,b}') == ['prea', 'preb']
        assert _expand_braces('file{1,2}.txt') == ['file1.txt', 'file2.txt']

    def test_with_suffix(self):
        assert _expand_braces('{a,b}suf') == ['asuf', 'bsuf']

    def test_cartesian_product(self):
        assert _expand_braces('{a,b}{1,2}') == ['a1', 'a2', 'b1', 'b2']

    def test_nested_braces(self):
        assert _expand_braces('{a,{b,c}}') == ['a', 'b', 'c']
        assert _expand_braces('{{a,b},{c,d}}') == ['a', 'b', 'c', 'd']

    def test_range_expansion(self):
        assert _expand_braces('{1..3}') == ['1', '2', '3']
        assert _expand_braces('file{1..3}.txt') == ['file1.txt', 'file2.txt', 'file3.txt']

    def test_no_expansion_needed(self):
        # Strings without braces return the literal string
        assert _expand_braces('hello') == ['hello']
        # Empty string returns empty list
        assert _expand_braces('') == []

    def test_single_item_no_expansion(self):
        # Single item (no comma) in braces doesn't expand - braces are literal
        assert _expand_braces('{a}') == ['{a}']
        # Empty braces are also literal (important for xargs -I{}, find -exec {})
        assert _expand_braces('{}') == ['{}']

    def test_escaped_braces(self):
        assert _expand_braces('\\{a,b\\}') == ['{a,b}']

    def test_unclosed_brace(self):
        # Unclosed braces are literal
        assert _expand_braces('{a,b') == ['{a,b']

    def test_complex_nested(self):
        # pre{a,b{1,2}}suf -> preasuf, preb1suf, preb2suf
        result = _expand_braces('pre{a,b{1,2}}suf')
        assert result == ['preasuf', 'preb1suf', 'preb2suf']
