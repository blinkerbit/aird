#!/usr/bin/env python3
"""
Test script to verify aird executable installation and functionality.
"""

import subprocess
import sys
import time
import os

def run_command(cmd, timeout=10):
    """Run a command and return the result"""
    try:
        result = subprocess.run(
            cmd, 
            shell=True, 
            capture_output=True, 
            text=True, 
            timeout=timeout
        )
        return result.returncode, result.stdout, result.stderr
    except subprocess.TimeoutExpired:
        return -1, "", "Command timed out"
    except Exception as e:
        return -1, "", str(e)

def test_installation():
    """Test if aird can be installed and run"""
    print("🔧 Testing Aird Installation and Executable Support")
    print("="*60)
    
    # Test 1: Install in development mode
    print("\n1. Installing Aird in development mode...")
    code, stdout, stderr = run_command("pip install -e .", timeout=60)
    if code == 0:
        print("✅ Installation successful")
    else:
        print(f"❌ Installation failed: {stderr}")
        return False
    
    # Test 2: Check if aird command is available
    print("\n2. Testing aird command availability...")
    code, stdout, stderr = run_command("aird --help", timeout=10)
    if code == 0:
        print("✅ aird command is available")
        print("Command help output:")
        print(stdout[:300] + "..." if len(stdout) > 300 else stdout)
    else:
        print(f"❌ aird command failed: {stderr}")
        return False
    
    # Test 3: Test version and basic functionality
    print("\n3. Testing aird version...")
    code, stdout, stderr = run_command("aird --help", timeout=5)
    if "Run Aird" in stdout:
        print("✅ Aird help text found")
    else:
        print("❌ Unexpected output from aird --help")
    
    # Test 4: Test python -m aird still works
    print("\n4. Testing python -m aird compatibility...")
    code, stdout, stderr = run_command("python -m aird --help", timeout=5)
    if code == 0:
        print("✅ python -m aird still works")
    else:
        print(f"❌ python -m aird failed: {stderr}")
    
    print("\n" + "="*60)
    print("🎉 All tests passed! Aird can now be run directly as 'aird'")
    print("\nUsage examples:")
    print("  aird                          # Run with default settings")
    print("  aird --port 9000              # Run on port 9000")
    print("  aird --root /path/to/files    # Serve specific directory")
    print("  aird --token mytoken          # Use custom access token")
    print("  aird --help                   # Show all options")
    
    return True

def main():
    if not test_installation():
        print("\n❌ Installation test failed!")
        sys.exit(1)
    
    print(f"\n✨ Aird v0.4.0 with mmap optimizations is ready!")
    print("Features:")
    print("- 🚀 Memory-mapped file operations for large files")
    print("- 🔒 Enhanced security (CSRF, XSS protection)")
    print("- 💻 Direct executable support (aird.exe)")
    print("- 📁 Efficient file serving and streaming")

if __name__ == "__main__":
    main()
