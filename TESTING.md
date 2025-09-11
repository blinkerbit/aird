# Testing Guide for Aird

This document describes how to run and work with the unit tests for the Aird project.

## Overview

The test suite provides comprehensive coverage for the Aird codebase, including:

- **Utility Functions**: File operations, path handling, icon mapping
- **Database Operations**: SQLite persistence, feature flags, shares management
- **Memory-Mapped File Handler**: Efficient file processing for large files
- **HTTP Handlers**: Authentication, file operations, admin interface
- **WebSocket Handlers**: Real-time feature flag updates and super search
- **Security Features**: Path validation, CSRF protection, input sanitization

## Quick Start

### Install Test Dependencies

```bash
# Install the package with test dependencies
pip install -e .[test]

# Or install manually
pip install -r requirements-test.txt
```

### Run All Tests

```bash
# Using the test runner script (recommended)
python run_tests.py --all

# Or using Make
make test

# Or directly with pytest
python -m pytest tests/ -v
```

## Test Runner Options

The `run_tests.py` script provides several options:

```bash
# Run tests with coverage reporting
python run_tests.py --coverage --html

# Run specific test pattern
python run_tests.py --pattern "test_database"

# Verbose output
python run_tests.py --verbose

# Install dependencies and run all checks
python run_tests.py --install --all

# Run only linting
python run_tests.py --lint
```

## Make Targets

The project includes a Makefile for common tasks:

```bash
make install       # Install package and test dependencies
make test          # Run all tests with coverage and linting
make test-quick    # Run tests without coverage (faster)
make test-verbose  # Run tests with verbose output
make lint          # Run code linting
make clean         # Clean up generated files
make build         # Build the package
```

## Test Structure

```
tests/
├── __init__.py
├── conftest.py                 # Shared fixtures and configuration
├── test_utilities.py           # Utility function tests
├── test_database.py           # Database operation tests
├── test_mmap_handler.py       # Memory-mapped file handler tests
├── test_handlers.py           # HTTP handler tests
├── test_file_operations.py    # File operation handler tests
└── test_websocket_handlers.py # WebSocket handler tests
```

## Test Categories

Tests are organized by markers:

- `@pytest.mark.unit` - Unit tests
- `@pytest.mark.integration` - Integration tests  
- `@pytest.mark.slow` - Slow-running tests
- `@pytest.mark.database` - Database-related tests
- `@pytest.mark.websocket` - WebSocket tests
- `@pytest.mark.security` - Security-focused tests

Run specific categories:

```bash
# Run only unit tests
python -m pytest tests/ -m unit

# Run database tests
python -m pytest tests/ -m database

# Skip slow tests
python -m pytest tests/ -m "not slow"
```

## Coverage Reporting

Generate coverage reports:

```bash
# Terminal coverage report
python -m pytest tests/ --cov=aird --cov-report=term-missing

# HTML coverage report
python -m pytest tests/ --cov=aird --cov-report=html

# View HTML report
open htmlcov/index.html
```

## Writing New Tests

### Test File Organization

1. Create test files following the pattern `test_<module_name>.py`
2. Group related tests in classes: `class TestClassName`
3. Use descriptive test method names: `test_function_behavior_condition`

### Example Test Structure

```python
"""
Unit tests for new_module in aird.main module.
"""

import pytest
from unittest.mock import patch, MagicMock
from aird.main import new_function


class TestNewFunction:
    """Test new_function behavior"""
    
    def setup_method(self):
        """Set up test fixtures"""
        self.test_data = "sample data"
    
    def teardown_method(self):
        """Clean up after test"""
        pass
    
    def test_basic_functionality(self):
        """Test basic function behavior"""
        result = new_function(self.test_data)
        assert result == expected_result
    
    def test_error_handling(self):
        """Test function handles errors gracefully"""
        with pytest.raises(ValueError):
            new_function(invalid_data)
    
    @patch('aird.main.some_dependency')
    def test_with_mocking(self, mock_dep):
        """Test function with mocked dependencies"""
        mock_dep.return_value = "mocked value"
        result = new_function()
        assert result == "expected with mock"
```

### Using Fixtures

The test suite provides several helpful fixtures:

```python
def test_with_temp_dir(temp_dir):
    """Test using temporary directory"""
    test_file = os.path.join(temp_dir, "test.txt")
    # temp_dir is automatically cleaned up

def test_with_sample_files(sample_files):
    """Test using pre-created sample files"""
    text_file = sample_files['text']
    py_file = sample_files['python']
    # Files are created in temp directory

def test_with_mock_db(mock_db_conn):
    """Test using mock database"""
    # Database is pre-initialized with tables
    pass
```

### Async Tests

For testing async functions:

```python
@pytest.mark.asyncio
async def test_async_function():
    """Test async function"""
    result = await async_function()
    assert result == expected
```

### WebSocket Tests

For WebSocket handler tests:

```python
class TestWebSocketHandler:
    @pytest.mark.asyncio
    async def test_websocket_message(self):
        handler = WebSocketHandler(app, request)
        handler.write_message = AsyncMock()
        
        await handler.on_message('{"test": "data"}')
        
        handler.write_message.assert_called_once()
```

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure package is installed in development mode:
   ```bash
   pip install -e .
   ```

2. **Permission Errors**: Tests create temporary files, ensure write permissions

3. **Database Errors**: Tests use in-memory SQLite, no external database needed

4. **Path Issues**: Tests mock ROOT_DIR, no need for specific directory structure

### Running Individual Tests

```bash
# Run specific test file
python -m pytest tests/test_utilities.py -v

# Run specific test class
python -m pytest tests/test_utilities.py::TestJoinPath -v

# Run specific test method
python -m pytest tests/test_utilities.py::TestJoinPath::test_join_path_basic -v
```

### Debug Mode

Enable debug output:

```bash
# Pytest debug output
python -m pytest tests/ -v -s --tb=long

# Python debug mode
python -m pytest tests/ --pdb
```

## CI/CD Integration

The test suite is designed to work with continuous integration:

```yaml
# Example GitHub Actions
- name: Install dependencies
  run: pip install -e .[test]

- name: Run tests
  run: python run_tests.py --all

- name: Upload coverage
  run: codecov
```

## Performance

- **Fast Tests**: Most unit tests run in milliseconds
- **Slow Tests**: File I/O and WebSocket tests may take longer
- **Parallel Execution**: Use `pytest-xdist` for parallel test execution:
  ```bash
  pip install pytest-xdist
  python -m pytest tests/ -n auto
  ```

## Contributing

When adding new features:

1. Write tests first (TDD approach)
2. Ensure >90% code coverage for new code
3. Test both success and failure cases
4. Add integration tests for complex features
5. Update this documentation if needed

## Resources

- [pytest Documentation](https://docs.pytest.org/)
- [pytest-asyncio](https://pytest-asyncio.readthedocs.io/)
- [pytest-tornado](https://github.com/eugeniy/pytest-tornado)
- [Coverage.py](https://coverage.readthedocs.io/)