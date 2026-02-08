"""Tests for shell argument expansion (brace, tilde, glob, raw opt-out)."""

import os
from pathlib import Path

import pytest

from shell.environment import env
from shell.model import prog, raw, run
from shell.model._command import _expand_arg, _expand_braces
from shell.model._types import RawArg


@pytest.fixture(autouse=True)
def init_shell_env():
    """Initialize shell environment for each test."""
    env.initialize()


class TestExpandArg:
    """Unit tests for _expand_arg helper."""

    def test_tilde_alone(self):
        assert _expand_arg('~') == [str(Path.home())]

    def test_tilde_slash_path(self):
        result = _expand_arg('~/Documents')
        assert result == [str(Path.home() / 'Documents')]

    def test_tilde_nested_path(self):
        result = _expand_arg('~/a/b/c')
        assert result == [str(Path.home() / 'a' / 'b' / 'c')]

    def test_no_expansion_mid_string(self):
        assert _expand_arg('foo~bar') == ['foo~bar']

    def test_no_expansion_tilde_no_slash(self):
        """~pattern without / is not expanded (not a home dir path)."""
        assert _expand_arg('~pattern') == ['~pattern']

    def test_plain_string_unchanged(self):
        assert _expand_arg('hello') == ['hello']

    def test_path_object_unchanged(self):
        """Path objects are stringified but not tilde-expanded."""
        p = Path('/some/path')
        assert _expand_arg(p) == ['/some/path']

    def test_int_arg(self):
        assert _expand_arg(42) == ['42']

    def test_raw_skips_tilde(self):
        assert _expand_arg(raw('~/Documents')) == ['~/Documents']

    def test_raw_skips_tilde_alone(self):
        assert _expand_arg(raw('~')) == ['~']

    def test_raw_plain_string(self):
        assert _expand_arg(raw('hello')) == ['hello']

    def test_raw_is_str_subclass(self):
        r = raw('hello')
        assert isinstance(r, str)
        assert isinstance(r, RawArg)


class TestGlobExpansion:
    """Unit tests for glob expansion in _expand_arg."""

    def test_glob_star_expands(self, tmp_path):
        """*.txt in a dir with .txt files should expand to matching filenames."""
        (tmp_path / 'a.txt').touch()
        (tmp_path / 'b.txt').touch()
        (tmp_path / 'c.py').touch()
        result = _expand_arg(f'{tmp_path}/*.txt')
        assert result == sorted([str(tmp_path / 'a.txt'), str(tmp_path / 'b.txt')])

    def test_glob_question_mark(self, tmp_path):
        """file?.txt pattern should match single-character wildcards."""
        (tmp_path / 'file1.txt').touch()
        (tmp_path / 'file2.txt').touch()
        (tmp_path / 'file10.txt').touch()  # Won't match ? (two chars)
        result = _expand_arg(f'{tmp_path}/file?.txt')
        assert result == sorted([str(tmp_path / 'file1.txt'), str(tmp_path / 'file2.txt')])

    def test_glob_bracket(self, tmp_path):
        """file[12].txt pattern should match bracket expressions."""
        (tmp_path / 'file1.txt').touch()
        (tmp_path / 'file2.txt').touch()
        (tmp_path / 'file3.txt').touch()
        result = _expand_arg(f'{tmp_path}/file[12].txt')
        assert result == sorted([str(tmp_path / 'file1.txt'), str(tmp_path / 'file2.txt')])

    def test_glob_no_match_returns_literal(self, tmp_path):
        """When no files match, the literal glob pattern is returned."""
        result = _expand_arg(f'{tmp_path}/*.xyz')
        assert result == [f'{tmp_path}/*.xyz']

    def test_glob_results_sorted(self, tmp_path):
        """Glob results are returned in sorted order."""
        for name in ['c.txt', 'a.txt', 'b.txt']:
            (tmp_path / name).touch()
        result = _expand_arg(f'{tmp_path}/*.txt')
        assert result == [str(tmp_path / 'a.txt'), str(tmp_path / 'b.txt'), str(tmp_path / 'c.txt')]

    def test_raw_skips_glob(self, tmp_path):
        """raw() prevents glob expansion."""
        (tmp_path / 'a.txt').touch()
        result = _expand_arg(raw(f'{tmp_path}/*.txt'))
        assert result == [f'{tmp_path}/*.txt']

    def test_tilde_glob_combo(self, tmp_path, monkeypatch):
        """~/somedir/*.txt should tilde-expand then glob-expand."""
        subdir = tmp_path / 'globtest'
        subdir.mkdir()
        (subdir / 'x.txt').touch()
        (subdir / 'y.txt').touch()
        monkeypatch.setenv('HOME', str(tmp_path))
        result = _expand_arg('~/globtest/*.txt')
        assert result == sorted([str(subdir / 'x.txt'), str(subdir / 'y.txt')])


class TestTildeExpansionIntegration:
    """Integration tests: tilde expansion through actual command execution."""

    def test_echo_tilde_expands(self):
        """echo ~ should print the home directory path."""
        result = run(prog('/bin/echo')('~'), silent=True)
        assert result.read_stdout() == str(Path.home())

    def test_echo_tilde_path_expands(self):
        """echo ~/foo should expand the tilde portion."""
        result = run(prog('/bin/echo')('~/foo'), silent=True)
        assert result.read_stdout() == str(Path.home() / 'foo')

    def test_echo_raw_tilde_preserved(self):
        """echo raw('~') should print literal ~."""
        result = run(prog('/bin/echo')(raw('~')), silent=True)
        assert result.read_stdout() == '~'

    def test_echo_raw_tilde_path_preserved(self):
        """echo raw('~/foo') should print literal ~/foo."""
        result = run(prog('/bin/echo')(raw('~/foo')), silent=True)
        assert result.read_stdout() == '~/foo'

    def test_ls_tilde_resolves(self):
        """ls ~ should list the home directory (no error)."""
        result = run(prog('ls')('~'), silent=True)
        assert result.exit_code == 0

    def test_tilde_in_pipeline(self):
        """Tilde expansion works for commands in a pipeline."""
        result = run(prog('/bin/echo')('~') | prog('cat')(), silent=True)
        assert result.read_stdout() == str(Path.home())

    def test_multiple_args_expand_independently(self):
        """Each argument is expanded independently."""
        result = run(prog('/bin/echo')('~', '~/foo', 'bar'), silent=True)
        home = str(Path.home())
        assert result.read_stdout() == f'{home} {home}/foo bar'

    def test_mixed_raw_and_normal(self):
        """raw() and normal args can be mixed freely."""
        result = run(prog('/bin/echo')(raw('~'), '~'), silent=True)
        assert result.read_stdout() == f'~ {Path.home()}'


class TestGlobExpansionIntegration:
    """Integration tests: glob expansion through actual command execution."""

    def test_echo_glob_expands(self, tmp_path):
        """echo *.txt should print expanded filenames."""
        (tmp_path / 'alpha.txt').touch()
        (tmp_path / 'beta.txt').touch()
        (tmp_path / 'gamma.py').touch()
        result = run(prog('/bin/echo')(f'{tmp_path}/*.txt'), silent=True)
        expected = f'{tmp_path}/alpha.txt {tmp_path}/beta.txt'
        assert result.read_stdout() == expected

    def test_echo_raw_glob_preserved(self, tmp_path):
        """echo raw('*.txt') should print the literal glob pattern."""
        (tmp_path / 'alpha.txt').touch()
        result = run(prog('/bin/echo')(raw(f'{tmp_path}/*.txt')), silent=True)
        assert result.read_stdout() == f'{tmp_path}/*.txt'

    def test_glob_no_match_passes_literal(self, tmp_path):
        """When glob matches nothing, the literal pattern is passed to the command."""
        result = run(prog('/bin/echo')(f'{tmp_path}/*.nope'), silent=True)
        assert result.read_stdout() == f'{tmp_path}/*.nope'

    def test_glob_in_pipeline(self, tmp_path):
        """Glob expansion works for commands in a pipeline."""
        (tmp_path / 'one.log').touch()
        (tmp_path / 'two.log').touch()
        result = run(
            prog('/bin/echo')(f'{tmp_path}/*.log') | prog('cat')(),
            silent=True,
        )
        expected = f'{tmp_path}/one.log {tmp_path}/two.log'
        assert result.read_stdout() == expected


class TestBraceExpansion:
    """Unit tests for brace expansion."""

    def test_comma_list(self):
        assert _expand_braces('{a,b,c}') == ['a', 'b', 'c']

    def test_prefix_suffix(self):
        assert _expand_braces('pre{a,b}suf') == ['preasuf', 'prebsuf']

    def test_nested_braces(self):
        assert _expand_braces('{a,{b,c}}') == ['a', 'b', 'c']

    def test_numeric_range(self):
        assert _expand_braces('{1..5}') == ['1', '2', '3', '4', '5']

    def test_alpha_range(self):
        assert _expand_braces('{a..e}') == ['a', 'b', 'c', 'd', 'e']

    def test_range_with_step(self):
        assert _expand_braces('{0..10..3}') == ['0', '3', '6', '9']

    def test_zero_padded_range(self):
        assert _expand_braces('{01..03}') == ['01', '02', '03']

    def test_reverse_range(self):
        assert _expand_braces('{5..1}') == ['5', '4', '3', '2', '1']

    def test_single_item_literal(self):
        """Single-item braces are not expanded."""
        assert _expand_braces('{a}') == ['{a}']

    def test_empty_braces_literal(self):
        """Empty braces are not expanded."""
        assert _expand_braces('{}') == ['{}']

    def test_no_braces_unchanged(self):
        assert _expand_braces('hello') == ['hello']

    def test_raw_skips_brace(self):
        """raw() prevents brace expansion."""
        assert _expand_arg(raw('{a,b}')) == ['{a,b}']

    def test_brace_then_tilde(self):
        """Brace expansion produces strings that get tilde-expanded."""
        home = str(Path.home())
        result = _expand_arg('{~,~/foo}')
        assert result == [home, f'{home}/foo']

    def test_brace_then_glob(self, tmp_path):
        """Brace expansion produces strings that get glob-expanded."""
        dir1 = tmp_path / 'dir1'
        dir2 = tmp_path / 'dir2'
        dir1.mkdir()
        dir2.mkdir()
        (dir1 / 'a.txt').touch()
        (dir2 / 'b.txt').touch()
        result = _expand_arg(f'{{{dir1},{dir2}}}/*.txt')
        assert result == sorted([str(dir1 / 'a.txt'), str(dir2 / 'b.txt')])

    def test_cartesian_product(self):
        """Multiple brace groups produce Cartesian product."""
        assert _expand_braces('{a,b}{1,2}') == ['a1', 'a2', 'b1', 'b2']

    def test_reverse_alpha_range(self):
        assert _expand_braces('{e..a}') == ['e', 'd', 'c', 'b', 'a']


class TestBraceExpansionIntegration:
    """Integration tests: brace expansion through actual command execution."""

    def test_echo_brace_comma(self):
        """echo {a,b,c} should print expanded comma list."""
        result = run(prog('/bin/echo')('{a,b,c}'), silent=True)
        assert result.read_stdout() == 'a b c'

    def test_echo_brace_range(self):
        """echo {1..5} should print expanded range."""
        result = run(prog('/bin/echo')('{1..5}'), silent=True)
        assert result.read_stdout() == '1 2 3 4 5'

    def test_echo_raw_brace_preserved(self):
        """raw() prevents brace expansion in commands."""
        result = run(prog('/bin/echo')(raw('{a,b,c}')), silent=True)
        assert result.read_stdout() == '{a,b,c}'
