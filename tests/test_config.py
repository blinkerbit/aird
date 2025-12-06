"""Tests for aird/config.py"""

import pytest
import os
import json
import tempfile
from unittest.mock import patch, MagicMock

# We need to be careful with importing config since it has side effects
# Import only what we need to test


class TestConfigureCloudProviders:
    """Tests for _configure_cloud_providers function"""
    
    def test_configure_with_none_config(self):
        """Test configuring with None config"""
        from aird.config import _configure_cloud_providers, CLOUD_MANAGER
        
        _configure_cloud_providers(None)
        # Should not raise and should reset cloud manager
    
    def test_configure_with_empty_config(self):
        """Test configuring with empty config"""
        from aird.config import _configure_cloud_providers, CLOUD_MANAGER
        
        _configure_cloud_providers({})
        # Should not raise
    
    def test_configure_google_drive_from_config(self):
        """Test configuring Google Drive from config dict"""
        from aird.config import _configure_cloud_providers, CLOUD_MANAGER
        
        config = {
            'cloud': {
                'google_drive': {
                    'access_token': 'test_token',
                    'root_id': 'test_root'
                }
            }
        }
        
        with patch('aird.config.GoogleDriveProvider') as mock_provider:
            mock_provider.return_value = MagicMock()
            _configure_cloud_providers(config)
            mock_provider.assert_called_once()
    
    def test_configure_google_drive_from_env(self):
        """Test configuring Google Drive from environment variables"""
        from aird.config import _configure_cloud_providers, CLOUD_MANAGER
        
        with patch.dict(os.environ, {'AIRD_GDRIVE_ACCESS_TOKEN': 'env_token'}):
            with patch('aird.config.GoogleDriveProvider') as mock_provider:
                mock_provider.return_value = MagicMock()
                _configure_cloud_providers({})
                mock_provider.assert_called_once()
    
    def test_configure_onedrive_from_config(self):
        """Test configuring OneDrive from config dict"""
        from aird.config import _configure_cloud_providers, CLOUD_MANAGER
        
        config = {
            'onedrive': {
                'access_token': 'test_token',
                'drive_id': 'test_drive'
            }
        }
        
        with patch('aird.config.OneDriveProvider') as mock_provider:
            mock_provider.return_value = MagicMock()
            _configure_cloud_providers(config)
            mock_provider.assert_called_once()
    
    def test_configure_onedrive_alternate_key(self):
        """Test configuring OneDrive with alternate 'one_drive' key"""
        from aird.config import _configure_cloud_providers
        
        config = {
            'one_drive': {
                'access_token': 'test_token'
            }
        }
        
        with patch('aird.config.OneDriveProvider') as mock_provider:
            mock_provider.return_value = MagicMock()
            _configure_cloud_providers(config)
            mock_provider.assert_called_once()
    
    def test_configure_onedrive_from_env(self):
        """Test configuring OneDrive from environment variable"""
        from aird.config import _configure_cloud_providers
        
        with patch.dict(os.environ, {'AIRD_ONEDRIVE_ACCESS_TOKEN': 'env_token'}):
            with patch('aird.config.OneDriveProvider') as mock_provider:
                mock_provider.return_value = MagicMock()
                _configure_cloud_providers({})
                mock_provider.assert_called_once()
    
    def test_configure_provider_error_handled(self):
        """Test that provider errors are logged but don't crash"""
        from aird.config import _configure_cloud_providers, CloudProviderError
        
        config = {
            'cloud': {
                'google_drive': {
                    'access_token': 'test_token'
                }
            }
        }
        
        with patch('aird.config.GoogleDriveProvider') as mock_provider:
            mock_provider.side_effect = CloudProviderError("Test error")
            # Should not raise
            _configure_cloud_providers(config)
    
    def test_configure_invalid_cloud_config_type(self):
        """Test that invalid cloud config type is handled"""
        from aird.config import _configure_cloud_providers
        
        config = {
            'cloud': "invalid_string"  # Should be dict
        }
        
        # Should not raise
        _configure_cloud_providers(config)


class TestModuleLevelVariables:
    """Tests for module-level configuration variables"""
    
    def test_default_values_exist(self):
        """Test that default configuration values are defined"""
        from aird import config
        
        assert hasattr(config, 'ROOT_DIR')
        assert hasattr(config, 'PORT')
        assert hasattr(config, 'ACCESS_TOKEN')
        assert hasattr(config, 'ADMIN_TOKEN')
        assert hasattr(config, 'LDAP_ENABLED')
        assert hasattr(config, 'FEATURE_FLAGS')
        assert hasattr(config, 'CLOUD_MANAGER')
    
    def test_default_ldap_disabled(self):
        """Test that LDAP is disabled by default"""
        from aird import config
        
        # Before init_config is called, LDAP should be disabled
        assert config.LDAP_ENABLED is False
    
    def test_feature_flags_is_dict(self):
        """Test that FEATURE_FLAGS is a dictionary"""
        from aird import config
        
        assert isinstance(config.FEATURE_FLAGS, dict)
    
    def test_cloud_manager_exists(self):
        """Test that CLOUD_MANAGER is initialized"""
        from aird import config
        
        assert config.CLOUD_MANAGER is not None


class TestInitConfig:
    """Tests for init_config function"""
    
    def test_init_config_with_config_file(self):
        """Test init_config with a config file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_data = {
                'root': '/test/root',
                'port': 9000,
                'token': 'test_token',
                'admin_token': 'admin_test_token',
                'features': {
                    'feature1': True,
                    'feature2': False
                }
            }
            json.dump(config_data, f)
            config_file = f.name
        
        try:
            with patch('sys.argv', ['test', '--config', config_file]):
                from aird import config
                # Reset module state
                config.CONFIG_FILE = None
                config.ROOT_DIR = os.getcwd()
                config.PORT = None
                config.FEATURE_FLAGS = {}
                
                config.init_config()
                
                assert config.CONFIG_FILE == config_file
                assert config.ROOT_DIR == '/test/root'
                assert config.PORT == 9000
                assert config.ACCESS_TOKEN == 'test_token'
                assert config.ADMIN_TOKEN == 'admin_test_token'
                assert config.FEATURE_FLAGS.get('feature1') is True
                assert config.FEATURE_FLAGS.get('feature2') is False
        finally:
            os.unlink(config_file)
    
    def test_init_config_command_line_args(self):
        """Test init_config with command line arguments"""
        with patch('sys.argv', ['test', '--root', '/cli/root', '--port', '8080', '--token', 'cli_token']):
            from aird import config
            # Reset module state
            config.CONFIG_FILE = None
            config.ROOT_DIR = os.getcwd()
            config.PORT = None
            
            config.init_config()
            
            assert config.ROOT_DIR == '/cli/root'
            assert config.PORT == 8080
            assert config.ACCESS_TOKEN == 'cli_token'
    
    def test_init_config_ldap_settings(self):
        """Test init_config with LDAP settings"""
        with patch('sys.argv', [
            'test',
            '--ldap',
            '--ldap-server', 'ldap.example.com',
            '--ldap-base-dn', 'dc=example,dc=com'
        ]):
            from aird import config
            config.LDAP_ENABLED = False
            config.LDAP_SERVER = None
            config.LDAP_BASE_DN = None
            
            config.init_config()
            
            assert config.LDAP_ENABLED is True
            assert config.LDAP_SERVER == 'ldap.example.com'
            assert config.LDAP_BASE_DN == 'dc=example,dc=com'
    
    def test_init_config_ssl_settings(self):
        """Test init_config with SSL settings"""
        with patch('sys.argv', [
            'test',
            '--ssl-cert', '/path/to/cert.pem',
            '--ssl-key', '/path/to/key.pem'
        ]):
            from aird import config
            config.SSL_CERT = None
            config.SSL_KEY = None
            
            config.init_config()
            
            assert config.SSL_CERT == '/path/to/cert.pem'
            assert config.SSL_KEY == '/path/to/key.pem'
    
    def test_init_config_generates_tokens_if_not_provided(self):
        """Test that tokens are generated if not provided"""
        with patch('sys.argv', ['test']):
            from aird import config
            config.ACCESS_TOKEN = None
            config.ADMIN_TOKEN = None
            
            with patch.dict(os.environ, {}, clear=True):
                # Remove any env vars that might provide tokens
                if 'AIRD_ACCESS_TOKEN' in os.environ:
                    del os.environ['AIRD_ACCESS_TOKEN']
                
                config.init_config()
                
                # Tokens should be generated
                assert config.ACCESS_TOKEN is not None
                assert len(config.ACCESS_TOKEN) > 0
                assert config.ADMIN_TOKEN is not None
                assert len(config.ADMIN_TOKEN) > 0
    
    def test_init_config_ldap_attributes_from_string(self):
        """Test that LDAP attributes can be provided as comma-separated string"""
        with patch('sys.argv', [
            'test',
            '--ldap-attributes', 'cn,mail,uid'
        ]):
            from aird import config
            config.LDAP_ATTRIBUTES = None
            
            config.init_config()
            
            assert config.LDAP_ATTRIBUTES == ['cn', 'mail', 'uid']
    
    def test_init_config_env_token(self):
        """Test that token can be provided via environment variable"""
        with patch('sys.argv', ['test']):
            with patch.dict(os.environ, {'AIRD_ACCESS_TOKEN': 'env_token_value'}):
                from aird import config
                config.ACCESS_TOKEN = None
                
                config.init_config()
                
                assert config.ACCESS_TOKEN == 'env_token_value'
    
    def test_init_config_default_port(self):
        """Test that default port is 8000"""
        with patch('sys.argv', ['test']):
            from aird import config
            config.PORT = None
            
            config.init_config()
            
            assert config.PORT == 8000
    
    def test_init_config_hostname(self):
        """Test hostname configuration"""
        with patch('sys.argv', ['test', '--hostname', 'custom.host.com']):
            from aird import config
            config.HOSTNAME = None
            
            config.init_config()
            
            assert config.HOSTNAME == 'custom.host.com'
    
    def test_init_config_admin_users(self):
        """Test admin_users configuration from config file"""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            config_data = {
                'admin_users': ['admin1', 'admin2']
            }
            json.dump(config_data, f)
            config_file = f.name
        
        try:
            with patch('sys.argv', ['test', '--config', config_file]):
                from aird import config
                config.ADMIN_USERS = []
                
                config.init_config()
                
                assert config.ADMIN_USERS == ['admin1', 'admin2']
        finally:
            os.unlink(config_file)


class TestConfigConstants:
    """Tests for configuration constants imported from constants module"""
    
    def test_max_file_size_imported(self):
        """Test that MAX_FILE_SIZE is imported"""
        from aird import config
        
        assert hasattr(config, 'MAX_FILE_SIZE')
        assert config.MAX_FILE_SIZE > 0
    
    def test_max_readable_file_size_imported(self):
        """Test that MAX_READABLE_FILE_SIZE is imported"""
        from aird import config
        
        assert hasattr(config, 'MAX_READABLE_FILE_SIZE')
        assert config.MAX_READABLE_FILE_SIZE > 0
    
    def test_allowed_upload_extensions_imported(self):
        """Test that ALLOWED_UPLOAD_EXTENSIONS is imported"""
        from aird import config
        
        assert hasattr(config, 'ALLOWED_UPLOAD_EXTENSIONS')
    
    def test_mmap_min_size_imported(self):
        """Test that MMAP_MIN_SIZE is imported"""
        from aird import config
        
        assert hasattr(config, 'MMAP_MIN_SIZE')
    
    def test_chunk_size_imported(self):
        """Test that CHUNK_SIZE is imported"""
        from aird import config
        
        assert hasattr(config, 'CHUNK_SIZE')
        assert config.CHUNK_SIZE > 0
