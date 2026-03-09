# Makefile for aird project

.PHONY: help install test test-coverage test-verbose test-quick test-serial lint clean build docs

# Default target
help:
	@echo "Available targets:"
	@echo "  install       - Install the package and test dependencies"
	@echo "  test          - Run all unit tests (parallel)"
	@echo "  test-serial   - Run all unit tests (single process)"
	@echo "  test-coverage - Run tests with coverage reporting"
	@echo "  test-verbose  - Run tests with verbose output (parallel)"
	@echo "  test-quick    - Run tests without coverage (parallel, minimal output)"
	@echo "  lint          - Run code linting"
	@echo "  clean         - Clean up generated files"
	@echo "  build         - Build the package"
	@echo "  all           - Run tests, coverage, and linting"

# Install package and dependencies
install:
	pip install -e .[test]

# Run all tests in parallel
test:
	python -m pytest tests/ -n auto -q

# Run all tests serially (useful for debugging flaky tests)
test-serial:
	python run_tests.py --all

# Run tests with coverage (serial — coverage needs single process)
test-coverage:
	python run_tests.py --coverage --html

# Run tests with verbose output in parallel
test-verbose:
	python -m pytest tests/ -n auto -v

# Run tests quickly in parallel (no coverage, minimal output)
test-quick:
	python -m pytest tests/ -n auto -q --no-header

# Run linting
lint:
	python run_tests.py --lint

# Clean up generated files
clean:
	rm -rf build/
	rm -rf dist/
	rm -rf *.egg-info/
	rm -rf htmlcov/
	rm -rf .coverage
	rm -rf .pytest_cache/
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type f -name "*.pyc" -delete

# Build package
build: clean
	python setup.py sdist bdist_wheel

# Run comprehensive checks
all: lint test-coverage