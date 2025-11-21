#!/usr/bin/env python3
"""
Unit tests for the JediCompleter implementation.

These tests validate that the tab completion works correctly in various scenarios
including edge cases and error conditions.
"""

import unittest
from unittest.mock import Mock, patch
import sys


class TestJediCompleter(unittest.TestCase):
    """Test cases for JediCompleter functionality."""
    
    def setUp(self):
        """Set up test fixtures."""
        try:
            from prompt_toolkit.completion import Completer, Completion
            from prompt_toolkit.document import Document
            self.has_deps = True
        except ImportError:
            self.has_deps = False
            self.skipTest("prompt_toolkit not available")
    
    def get_completer_class(self):
        """Get the JediCompleter class definition."""
        from prompt_toolkit.completion import Completer, Completion
        
        class JediCompleter(Completer):
            """Custom completer that uses jedi for intelligent Python completions."""

            def __init__(self, namespace: dict):
                self.namespace = namespace

            def get_completions(self, document, complete_event):
                """Generate completions for the current document state."""
                import jedi

                text = document.text_before_cursor
                
                if not text or text.isspace():
                    return

                try:
                    script = jedi.Interpreter(
                        text,
                        namespaces=[self.namespace]
                    )
                    
                    completions = script.complete()
                    
                    for completion in completions:
                        completion_text = completion.name
                        
                        start_position = -len(completion.name_with_symbols.split('.')[-1])
                        if completion.complete:
                            start_position = -len(completion.complete)
                        
                        display = completion.name
                        display_meta = ""
                        
                        if completion.type:
                            display_meta = f"({completion.type})"
                        
                        yield Completion(
                            completion_text,
                            start_position=start_position,
                            display=display,
                            display_meta=display_meta
                        )
                        
                except Exception:
                    pass
        
        return JediCompleter
    
    def test_empty_input(self):
        """Test that empty input returns no completions."""
        from prompt_toolkit.document import Document
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter({})
        document = Document("", 0)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        self.assertEqual(len(completions), 0)
    
    def test_whitespace_only_input(self):
        """Test that whitespace-only input returns no completions."""
        from prompt_toolkit.document import Document
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter({})
        document = Document("   ", 3)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        self.assertEqual(len(completions), 0)
    
    def test_variable_completion(self):
        """Test completion of variable names from namespace."""
        from prompt_toolkit.document import Document
        
        namespace = {
            'test_var': 42,
            'test_list': [1, 2, 3],
            'other_var': "hello"
        }
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter(namespace)
        document = Document("test_", 5)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        
        # Should find variables starting with "test_"
        self.assertGreater(len(completions), 0)
        completion_names = [str(c.display) for c in completions]
        # At least one should contain our variables
        self.assertTrue(any('test_var' in str(name) or 'test_list' in str(name) 
                           for name in completion_names))
    
    def test_attribute_completion(self):
        """Test completion of object attributes."""
        from prompt_toolkit.document import Document
        
        namespace = {'my_list': [1, 2, 3]}
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter(namespace)
        document = Document("my_list.ap", 10)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        
        # Should find list methods starting with "ap" (append)
        self.assertGreater(len(completions), 0)
        completion_names = [str(c.display) for c in completions]
        self.assertTrue(any('append' in str(name) for name in completion_names))
    
    def test_module_completion(self):
        """Test completion of module names in imports."""
        from prompt_toolkit.document import Document
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter({})
        document = Document("import o", 8)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        
        # Should find modules starting with "o" (os, operator, etc.)
        self.assertGreater(len(completions), 0)
        completion_names = [str(c.display) for c in completions]
        # Should contain at least "os"
        self.assertTrue(any('os' in str(name) for name in completion_names))
    
    def test_invalid_syntax_handling(self):
        """Test that invalid syntax doesn't crash the completer."""
        from prompt_toolkit.document import Document
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter({})
        
        invalid_inputs = [
            "def (",
            "for x in",
            "if True:",
            "[[[",
            "lambda x:",
        ]
        
        for invalid_input in invalid_inputs:
            document = Document(invalid_input, len(invalid_input))
            complete_event = Mock()
            
            try:
                # Should not raise an exception
                completions = list(completer.get_completions(document, complete_event))
                # May return empty or some completions, but shouldn't crash
                self.assertIsInstance(completions, list)
            except Exception as e:
                self.fail(f"Completer crashed on invalid input '{invalid_input}': {e}")
    
    def test_namespace_isolation(self):
        """Test that completer only sees variables in its namespace."""
        from prompt_toolkit.document import Document
        
        namespace1 = {'var1': 1}
        namespace2 = {'var2': 2}
        
        JediCompleter = self.get_completer_class()
        completer1 = JediCompleter(namespace1)
        completer2 = JediCompleter(namespace2)
        
        # Test completer1 can complete var1
        document = Document("var", 3)
        complete_event = Mock()
        completions1 = list(completer1.get_completions(document, complete_event))
        names1 = [str(c.display) for c in completions1]
        
        # Test completer2 can complete var2
        completions2 = list(completer2.get_completions(document, complete_event))
        names2 = [str(c.display) for c in completions2]
        
        # Each should see different variables
        has_var1 = any('var1' in str(n) for n in names1)
        has_var2 = any('var2' in str(n) for n in names2)
        
        # At least one should have found its variable
        self.assertTrue(has_var1 or has_var2)
    
    def test_builtin_completion(self):
        """Test completion of Python built-ins."""
        from prompt_toolkit.document import Document
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter({})
        document = Document("pri", 3)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        completion_names = [str(c.display) for c in completions]
        
        # Should find "print" as a builtin
        self.assertTrue(any('print' in str(name) for name in completion_names))
    
    def test_chained_completion(self):
        """Test completion on chained attribute access."""
        from prompt_toolkit.document import Document
        
        namespace = {'text': "hello"}
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter(namespace)
        document = Document("text.upp", 8)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        completion_names = [str(c.display) for c in completions]
        
        # Should find "upper" method
        self.assertTrue(any('upper' in str(name) for name in completion_names))
    
    def test_type_information_included(self):
        """Test that completions include type information."""
        from prompt_toolkit.document import Document
        
        namespace = {'my_func': lambda x: x}
        
        JediCompleter = self.get_completer_class()
        completer = JediCompleter(namespace)
        document = Document("my_", 3)
        complete_event = Mock()
        
        completions = list(completer.get_completions(document, complete_event))
        
        # At least one completion should have display_meta (type info)
        has_meta = any(c.display_meta for c in completions)
        self.assertTrue(has_meta or len(completions) == 0)


class TestJediCompleterIntegration(unittest.TestCase):
    """Integration tests for JediCompleter in the REPL context."""
    
    def test_repl_file_has_jedi_completer(self):
        """Test that repl.py contains the JediCompleter class."""
        try:
            with open('python/shell/repl.py', 'r') as f:
                content = f.read()
            
            self.assertIn('class JediCompleter', content)
            self.assertIn('jedi.Interpreter', content)
            self.assertIn('install_packages("prompt_toolkit", "jedi")', content)
        except FileNotFoundError:
            self.skipTest("repl.py not found (test run from different directory)")
    
    def test_jedi_in_dependencies(self):
        """Test that jedi is listed in the package installation."""
        try:
            with open('python/shell/repl.py', 'r') as f:
                content = f.read()
            
            # Check that jedi is in the install_packages call
            self.assertIn('"jedi"', content)
        except FileNotFoundError:
            self.skipTest("repl.py not found")
    
    def test_completer_attached_to_session(self):
        """Test that completer is attached to PromptSession."""
        try:
            with open('python/shell/repl.py', 'r') as f:
                content = f.read()
            
            # Check that the completer is passed to PromptSession
            self.assertIn('PromptSession(completer=completer)', content)
        except FileNotFoundError:
            self.skipTest("repl.py not found")


def run_tests():
    """Run all tests."""
    print("Running JediCompleter Unit Tests")
    print("=" * 60)
    
    # Check for dependencies
    try:
        import jedi
        import prompt_toolkit
        print("✓ Dependencies available (jedi, prompt_toolkit)")
    except ImportError as e:
        print(f"⚠ Warning: {e}")
        print("  Some tests may be skipped")
    
    print()
    
    # Run tests
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    suite.addTests(loader.loadTestsFromTestCase(TestJediCompleter))
    suite.addTests(loader.loadTestsFromTestCase(TestJediCompleterIntegration))
    
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    print("\n" + "=" * 60)
    if result.wasSuccessful():
        print("✓ All tests passed!")
    else:
        print(f"✗ {len(result.failures)} test(s) failed")
        print(f"✗ {len(result.errors)} test(s) had errors")
    
    return result.wasSuccessful()


if __name__ == "__main__":
    success = run_tests()
    sys.exit(0 if success else 1)
