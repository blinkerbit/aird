#!/usr/bin/env python3
"""
Test runner script for aird unit tests.

This script provides an easy way to run all tests with different options
and generate coverage reports.
"""

import sys
import subprocess
import argparse
import os


def run_command(cmd, description=""):
    """Run a command and return success status"""
    if description:
        print(f"\n{description}")
        print("=" * 60)
    
    print(f"Running: {cmd}")
    result = subprocess.run(cmd, shell=True)
    
    if result.returncode == 0:
        print(f"✅ {description} completed successfully")
    else:
        print(f"❌ {description} failed with code {result.returncode}")
    
    return result.returncode == 0


def install_test_dependencies():
    """Install test dependencies"""
    return run_command(
        "pip install -e .[test]",
        "Installing test dependencies"
    )


def run_tests(coverage=False, verbose=False, pattern=None, html_report=False):
    """Run the test suite"""
    cmd_parts = ["python", "-m", "pytest"]
    
    if verbose:
        cmd_parts.append("-v")
    
    if coverage:
        cmd_parts.extend(["--cov=aird", "--cov-report=term-missing"])
        if html_report:
            cmd_parts.append("--cov-report=html")
    
    if pattern:
        cmd_parts.extend(["-k", pattern])
    
    cmd_parts.append("tests/")
    
    cmd = " ".join(cmd_parts)
    return run_command(cmd, "Running unit tests")


def run_linting():
    """Run code linting (if available)"""
    # Try to run flake8 if available
    try:
        return run_command("flake8 aird/ tests/ --max-line-length=120", "Running code linting")
    except:
        print("⚠️  Linting tools not available, skipping...")
        return True


def generate_coverage_report():
    """Generate detailed coverage report"""
    return run_command("coverage html", "Generating HTML coverage report")


def main():
    parser = argparse.ArgumentParser(description="Run aird unit tests")
    parser.add_argument("--coverage", "-c", action="store_true", 
                       help="Run tests with coverage reporting")
    parser.add_argument("--html", action="store_true",
                       help="Generate HTML coverage report (requires --coverage)")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Verbose test output")
    parser.add_argument("--pattern", "-k", type=str,
                       help="Run only tests matching this pattern")
    parser.add_argument("--install", "-i", action="store_true",
                       help="Install test dependencies first")
    parser.add_argument("--lint", "-l", action="store_true",
                       help="Run linting checks")
    parser.add_argument("--all", "-a", action="store_true",
                       help="Run all checks (tests, coverage, linting)")
    
    args = parser.parse_args()
    
    # Install dependencies if requested
    if args.install or args.all:
        if not install_test_dependencies():
            print("❌ Failed to install dependencies")
            return 1
    
    success = True
    
    # Run linting if requested
    if args.lint or args.all:
        if not run_linting():
            success = False
    
    # Run tests
    coverage = args.coverage or args.all
    html_report = args.html or args.all
    
    if not run_tests(coverage=coverage, verbose=args.verbose, 
                    pattern=args.pattern, html_report=html_report):
        success = False
    
    # Generate additional reports if requested
    if html_report and coverage:
        if not generate_coverage_report():
            success = False
        else:
            print("\n📊 HTML coverage report generated in htmlcov/index.html")
    
    if success:
        print("\n🎉 All tests completed successfully!")
        return 0
    else:
        print("\n❌ Some tests or checks failed!")
        return 1


if __name__ == "__main__":
    sys.exit(main())