#!/usr/bin/env python3
"""
Simple test runner for the rewritten aird test suite.

This script provides a convenient way to run tests with proper environment setup
and handles cases where the main aird module is not available.
"""

import sys
import os
import subprocess
import argparse
from pathlib import Path


def main():
    """Main test runner function"""
    parser = argparse.ArgumentParser(description="Run aird test suite")
    parser.add_argument("--verbose", "-v", action="store_true",
                       help="Run tests with verbose output")
    parser.add_argument("--coverage", "-c", action="store_true",
                       help="Run tests with coverage reporting")
    parser.add_argument("--pattern", "-p", type=str,
                       help="Run tests matching pattern")
    parser.add_argument("--markers", "-m", type=str,
                       help="Run tests with specific markers (e.g., 'unit', 'integration')")
    parser.add_argument("--failfast", "-x", action="store_true",
                       help="Stop on first failure")
    
    args = parser.parse_args()
    
    # Check if we're in the right directory
    if not Path("tests").exists():
        print("Error: tests directory not found. Run from project root.")
        sys.exit(1)
    
    # Build pytest command
    cmd = [sys.executable, "-m", "pytest", "tests/"]
    
    if args.verbose:
        cmd.append("-v")
    
    if args.failfast:
        cmd.append("-x")
    
    if args.pattern:
        cmd.extend(["-k", args.pattern])
    
    if args.markers:
        cmd.extend(["-m", args.markers])
    
    if args.coverage:
        cmd.extend(["--cov=aird", "--cov-report=term-missing"])
    
    # Add other useful options
    cmd.extend(["--tb=short", "--strict-markers"])
    
    print(f"Running: {' '.join(cmd)}")
    print("-" * 60)
    
    try:
        result = subprocess.run(cmd, check=False)
        return result.returncode
    except KeyboardInterrupt:
        print("\nTest run interrupted by user")
        return 130
    except FileNotFoundError:
        print("Error: pytest not found. Install test dependencies first:")
        print("  uv pip install -r requirements-test.txt")
        return 1


if __name__ == "__main__":
    sys.exit(main())
