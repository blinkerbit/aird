---
name: Increase Test Coverage to 80%
about: Help improve code quality by writing tests for uncovered code
title: '[Testing] Increase test coverage from 70% to 80%'
labels: ['good first issue', 'testing', 'help wanted']
assignees: ''
---

## 🎯 Goal
Increase the overall test coverage of the project from **70% to 80%** by writing unit tests for currently uncovered code.

## 📊 Current Status
- **Current Coverage**: ~70%
- **Target Coverage**: 80%
- **Gap**: ~10% (approximately 500-800 lines of code need test coverage)

## 🔍 How to Get Started

### 1. Check Current Coverage
Run the test suite with coverage reporting:
```bash
python -m pytest --cov=aird --cov-report=html
```

This will generate an HTML coverage report in `htmlcov/index.html` that you can open in your browser to see which files and lines need coverage.

### 2. Identify Low-Coverage Files
Look for files with coverage below 80%. Priority areas include:
- `aird/handlers/*.py` - Request handlers
- `aird/utils/*.py` - Utility functions
- `aird/core/*.py` - Core functionality
- `aird/db.py` - Database operations

### 3. Write Tests
For each uncovered function or code path:
1. Create or update the corresponding test file in `tests/`
2. Follow the existing test patterns (see examples below)
3. Aim for meaningful tests that verify behavior, not just line coverage

## 📝 Test Writing Guidelines

### Example Test Structure
```python
import pytest
from aird.handlers.example_handler import ExampleHandler

class TestExampleHandler:
    def setup_method(self):
        # Setup code runs before each test
        self.handler = ExampleHandler()
    
    def test_basic_functionality(self):
        # Arrange
        input_data = "test"
        
        # Act
        result = self.handler.process(input_data)
        
        # Assert
        assert result == "expected_output"
    
    @pytest.mark.asyncio
    async def test_async_method(self):
        # For async methods
        result = await self.handler.async_process()
        assert result is not None
```

### What to Test
- ✅ **Happy paths**: Normal expected behavior
- ✅ **Edge cases**: Empty inputs, None values, boundary conditions
- ✅ **Error handling**: Exception cases, validation failures
- ✅ **Integration points**: Database operations, file I/O, API calls

### What NOT to Test
- ❌ Third-party library internals
- ❌ Simple getters/setters without logic
- ❌ Configuration constants

## 🎓 Resources for Beginners

### Running Tests
```bash
# Run all tests
python -m pytest

# Run specific test file
python -m pytest tests/test_example.py

# Run with coverage for specific module
python -m pytest --cov=aird.handlers tests/test_handlers.py

# Run tests matching a pattern
python -m pytest -k "test_user"
```

### Useful Pytest Features
- `@pytest.fixture` - Reusable test setup
- `@pytest.mark.parametrize` - Run same test with different inputs
- `@pytest.mark.asyncio` - Test async functions
- `pytest.raises()` - Assert exceptions are raised
- `unittest.mock.patch` - Mock external dependencies

### Example Files to Reference
- `tests/test_base_handler.py` - Handler testing patterns
- `tests/test_admin_handlers.py` - Complex handler tests with mocking
- `tests/test_view_handlers.py` - Async handler tests

## 📋 Suggested Approach

### For Newcomers
1. **Start small**: Pick a single file with <50% coverage
2. **Read the code**: Understand what the function does
3. **Write 2-3 tests**: Cover the main use cases
4. **Submit a PR**: Get feedback early and often
5. **Iterate**: Address review comments and learn

### Breaking Down the Work
This issue can be tackled incrementally. Each PR can focus on:
- A single module (e.g., `test_file_handlers.py`)
- A single class (e.g., `TestUserCreateHandler`)
- Even a single function (for complex functions)

**Small PRs are encouraged!** They're easier to review and merge.

## ✅ Acceptance Criteria
- [ ] Overall test coverage reaches 80% or higher
- [ ] All new tests pass consistently
- [ ] Tests follow existing project patterns
- [ ] No decrease in coverage for already-tested code
- [ ] Tests are meaningful (not just hitting lines for coverage sake)

## 🤝 How to Contribute

1. **Comment on this issue** to let others know which file/module you're working on
2. **Fork the repository** and create a feature branch
3. **Write your tests** following the guidelines above
4. **Run the test suite** to ensure everything passes
5. **Submit a Pull Request** with:
   - Clear description of what you tested
   - Before/after coverage percentages for the file(s)
   - Reference to this issue (e.g., "Closes #XXX")

## 💬 Questions?
Feel free to ask questions in the comments! We're here to help newcomers learn testing best practices.

## 🏆 Recognition
Contributors who help reach the 80% goal will be acknowledged in the project README!

---

**Labels**: `good first issue`, `testing`, `help wanted`  
**Difficulty**: Beginner to Intermediate  
**Time Estimate**: 2-4 hours per module (varies by complexity)
