#!/bin/bash

echo "============================================================"
echo "Tab Completion Implementation Verification"
echo "============================================================"
echo ""

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

success=0
total=0

check_file() {
    total=$((total + 1))
    if [ -f "$1" ]; then
        echo -e "${GREEN}✓${NC} File exists: $1"
        success=$((success + 1))
        return 0
    else
        echo -e "${RED}✗${NC} File missing: $1"
        return 1
    fi
}

check_content() {
    total=$((total + 1))
    if grep -q "$2" "$1" 2>/dev/null; then
        echo -e "${GREEN}✓${NC} $3"
        success=$((success + 1))
        return 0
    else
        echo -e "${RED}✗${NC} $3"
        return 1
    fi
}

echo "Checking Modified Files..."
echo "------------------------------------------------------------"
check_file "python/shell/repl.py"
check_file "README.md"
echo ""

echo "Checking New Documentation..."
echo "------------------------------------------------------------"
check_file "docs/TAB_COMPLETION.md"
check_file "docs/TAB_COMPLETION_QUICKREF.md"
check_file "IMPLEMENTATION_SUMMARY.md"
check_file "FEATURE_CHECKLIST.md"
check_file "CHANGELOG_TAB_COMPLETION.md"
echo ""

echo "Checking Examples and Tests..."
echo "------------------------------------------------------------"
check_file "examples/tab_completion_demo.py"
check_file "test_tab_completion.py"
check_file "tests/test_jedi_completer.py"
echo ""

echo "Checking Implementation Details..."
echo "------------------------------------------------------------"
check_content "python/shell/repl.py" "class JediCompleter" "JediCompleter class defined"
check_content "python/shell/repl.py" "jedi.Interpreter" "Using jedi.Interpreter"
check_content "python/shell/repl.py" '"jedi"' "Jedi in package list"
check_content "python/shell/repl.py" "PromptSession(completer=completer)" "Completer attached to session"
echo ""

echo "Checking Documentation Content..."
echo "------------------------------------------------------------"
check_content "README.md" "Tab Completion" "README mentions tab completion"
check_content "docs/TAB_COMPLETION.md" "jedi" "Documentation mentions jedi"
check_content "IMPLEMENTATION_SUMMARY.md" "JediCompleter" "Summary documents implementation"
echo ""

echo "Running Tests..."
echo "------------------------------------------------------------"
if [ -f "test_tab_completion.py" ]; then
    if python3 test_tab_completion.py > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} test_tab_completion.py passes"
        success=$((success + 1))
    else
        echo -e "${RED}✗${NC} test_tab_completion.py failed"
    fi
    total=$((total + 1))
fi

if [ -f "tests/test_jedi_completer.py" ]; then
    if python3 tests/test_jedi_completer.py > /dev/null 2>&1; then
        echo -e "${GREEN}✓${NC} tests/test_jedi_completer.py passes"
        success=$((success + 1))
    else
        echo -e "${RED}✗${NC} tests/test_jedi_completer.py failed"
    fi
    total=$((total + 1))
fi
echo ""

echo "============================================================"
echo "Verification Results"
echo "============================================================"
echo -e "Checks passed: ${GREEN}$success${NC} / $total"
echo ""

if [ $success -eq $total ]; then
    echo -e "${GREEN}✓ All checks passed! Implementation is complete.${NC}"
    exit 0
else
    failed=$((total - success))
    echo -e "${YELLOW}⚠ $failed check(s) failed. Please review above.${NC}"
    exit 1
fi
