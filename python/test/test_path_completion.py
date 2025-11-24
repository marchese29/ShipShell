#!/usr/bin/env python3
"""Test script to verify path completion works correctly in various scenarios."""

from python.shell._repl_internal import _detect_string_context


def test_string_detection():
    """Test the string context detection function."""

    print("Testing _detect_string_context function...")
    print("=" * 60)

    # Test 1: Simple string
    text = "prog('cat')('/etc/"
    result = _detect_string_context(text)
    print("\nTest 1: Simple string")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (True, '/etc/', 5)")
    assert result[0] is True, "Should detect string"
    assert result[1] == "/etc/", f"Expected '/etc/', got '{result[1]}'"

    # Test 2: Not in a string
    text = "some_var.met"
    result = _detect_string_context(text)
    print("\nTest 2: Not in a string")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (False, '', 0)")
    assert result[0] is False, "Should not detect string"

    # Test 3: Inside f-string braces (should NOT complete)
    text = 'f"Value: {some_var'
    result = _detect_string_context(text)
    print("\nTest 3: Inside f-string braces")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (False, '', 0)")
    assert result[0] is False, "Should not complete inside f-string braces"

    # Test 4: f-string but not in braces (should complete)
    text = 'f"Path: /home/'
    result = _detect_string_context(text)
    print("\nTest 4: f-string but outside braces")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (True, 'Path: /home/', ...)")
    assert result[0] is True, "Should complete in f-string outside braces"
    assert "/home/" in result[1], f"Expected '/home/' in path, got '{result[1]}'"

    # Test 5: Double quotes
    text = 'prog("ls")("/tmp/'
    result = _detect_string_context(text)
    print("\nTest 5: Double quotes")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (True, '/tmp/', 5)")
    assert result[0] is True, "Should detect string"
    assert result[1] == "/tmp/", f"Expected '/tmp/', got '{result[1]}'"

    # Test 6: After closing brace in f-string
    text = 'f"{var} /etc/'
    result = _detect_string_context(text)
    print("\nTest 6: After closing brace in f-string")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (True, ..., ...)")
    assert result[0] is True, "Should complete after closing brace"
    assert "/etc/" in result[1], f"Expected '/etc/' in path, got '{result[1]}'"

    # Test 7: Nested braces in f-string
    text = "f\"{data['key']"
    result = _detect_string_context(text)
    print("\nTest 7: Nested braces/quotes in f-string")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (False, '', 0)")
    assert result[0] is False, "Should not complete inside f-string braces"

    # Test 8: Escaped braces in f-string
    text = 'f"{{escaped}} /home/'
    result = _detect_string_context(text)
    print("\nTest 8: Escaped braces in f-string")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (True, ..., ...)")
    assert result[0] is True, "Should complete with escaped braces"
    assert "/home/" in result[1], f"Expected '/home/' in path, got '{result[1]}'"

    # Test 9: Raw string
    text = r"r'/path/to/file"
    result = _detect_string_context(text)
    print("\nTest 9: Raw string")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (True, '/path/to/file', ...)")
    assert result[0] is True, "Should detect raw string"
    assert "/path/to/file" in result[1], (
        f"Expected '/path/to/file' in path, got '{result[1]}'"
    )

    # Test 10: String closed (should NOT complete)
    text = "prog('cat')('/etc/hosts') "
    result = _detect_string_context(text)
    print("\nTest 10: Closed string")
    print(f"  Input: {text}")
    print(f"  Result: {result}")
    print("  Expected: (False, '', 0)")
    assert result[0] is False, "Should not complete outside string"

    print("\n" + "=" * 60)
    print("All tests passed! ✓")


if __name__ == "__main__":
    test_string_detection()
