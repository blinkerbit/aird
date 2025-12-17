
import pytest
from unittest.mock import patch, MagicMock
import hashlib
import secrets
from aird.db import hash_password, verify_password

def test_argon2_hashing():
    """Test that Argon2 is used when available"""
    # Assuming ARGON2_AVAILABLE is True in this environment
    password = "secure_password"
    hashed = hash_password(password)
    assert hashed.startswith("$argon2")
    assert verify_password(password, hashed)
    assert not verify_password("wrong_password", hashed)

def test_scrypt_fallback():
    """Test Scrypt fallback when Argon2 is not available"""
    with patch('aird.db.ARGON2_AVAILABLE', False):
        password = "secure_password"
        # Force reload or adjust the module variable if needed. 
        # Since we patched the imported name in aird.db (if it was imported that way), 
        # or we patch it where it is used.
        # However, aird.db imports it. Let's patch 'aird.db.ARGON2_AVAILABLE'.
        
        hashed = hash_password(password)
        assert hashed.startswith("scrypt:")
        assert verify_password(password, hashed)
        assert not verify_password("wrong_password", hashed)

def test_legacy_sha256_verification():
    """Test that legacy SHA-256 hashes can still be verified"""
    password = "legacy_password"
    salt = secrets.token_hex(32)
    pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    legacy_hash = f"{salt}:{pwd_hash}"
    
    assert verify_password(password, legacy_hash)
    assert not verify_password("wrong_password", legacy_hash)

def test_verify_password_edge_cases():
    assert not verify_password("pass", None)
    assert not verify_password("pass", "")
    assert not verify_password("pass", "invalid:format:too:many:colons")
