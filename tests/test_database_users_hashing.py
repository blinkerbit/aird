
import pytest
from unittest.mock import patch
import hashlib
import secrets
# Import from the OTHER module this time
from aird.database.users import hash_password, verify_password, ARGON2_AVAILABLE

def test_argon2_hashing_users_module():
    """Test that Argon2 is used when available (users module)"""
    password = "secure_password"
    hashed = hash_password(password)
    if ARGON2_AVAILABLE:
        assert hashed.startswith("$argon2")
    else:
        # If argon2 not available in env, it should fallback to scrypt
        assert hashed.startswith("scrypt:")
    
    assert verify_password(password, hashed)
    assert not verify_password("wrong_password", hashed)

def test_scrypt_fallback_users_module():
    """Test Scrypt fallback when Argon2 is not available (users module)"""
    with patch('aird.database.users.ARGON2_AVAILABLE', False):
        password = "secure_password"
        hashed = hash_password(password)
        assert hashed.startswith("scrypt:")
        assert verify_password(password, hashed)
        assert not verify_password("wrong_password", hashed)

def test_legacy_sha256_verification_users_module():
    """Test that legacy SHA-256 hashes can still be verified (users module)"""
    password = "legacy_password"
    salt = secrets.token_hex(32)
    pwd_hash = hashlib.sha256((salt + password).encode('utf-8')).hexdigest()
    legacy_hash = f"{salt}:{pwd_hash}"
    
    assert verify_password(password, legacy_hash)
    assert not verify_password("wrong_password", legacy_hash)
