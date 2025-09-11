# Makefile for aird project

.PHONY: help install test test-coverage test-verbose test-quick lint clean build docs

# Default target
help:
	@echo "Available targets:"
	@echo "  install       - Install the package and test dependencies"
	@echo "  test          - Run all unit tests"
	@echo "  test-coverage - Run tests with coverage reporting"
	@echo "  test-verbose  - Run tests with verbose output"
	@echo "  test-quick    - Run tests without coverage (faster)"
	@echo "  lint          - Run code linting"
	@echo "  clean         - Clean up generated files"
	@echo "  build         - Build the package"
	@echo "  all           - Run tests, coverage, and linting"

# Install package and dependencies
install:
	pip install -e .[test]

# Run all tests
test:
	python run_tests.py --all

# Run tests with coverage
test-coverage:
	python run_tests.py --coverage --html

# Run tests with verbose output
test-verbose:
	python run_tests.py --verbose

# Run tests quickly (no coverage)
test-quick:
	python -m pytest tests/ -v

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