
import pytest
from unittest.mock import MagicMock, patch
import time

try:
    from aird.handlers.auth_handlers import check_login_rate_limit, _LOGIN_ATTEMPTS
    from aird.constants import LOGIN_RATE_LIMIT_ATTEMPTS, LOGIN_RATE_LIMIT_WINDOW
    AIRD_AVAILABLE = True
except ImportError:
    AIRD_AVAILABLE = False

@pytest.mark.skipif(not AIRD_AVAILABLE, reason="aird module not available")
class TestRateLimiting:
    def setup_method(self):
        if AIRD_AVAILABLE:
            _LOGIN_ATTEMPTS.clear()

    def test_rate_limit_counters(self):
        ip = "1.2.3.4"
        for _ in range(LOGIN_RATE_LIMIT_ATTEMPTS):
            assert check_login_rate_limit(ip) is True
        
        # Next one should fail
        assert check_login_rate_limit(ip) is False

    def test_rate_limit_expiry(self):
        ip = "5.6.7.8"
        # Use up quota
        for _ in range(LOGIN_RATE_LIMIT_ATTEMPTS):
            check_login_rate_limit(ip)
        
        assert check_login_rate_limit(ip) is False
        
        # Mock time to be in future
        # Note: We need to ensure we patch the time function used in the module
        future_time = time.time() + LOGIN_RATE_LIMIT_WINDOW + 10
        with patch('aird.handlers.auth_handlers.time.time', return_value=future_time):
            # Should work now because window expired
            assert check_login_rate_limit(ip) is True
