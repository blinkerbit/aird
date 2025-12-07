"""
Tests for aird.core.websocket_manager module.
"""

import pytest
import time
from unittest.mock import patch, MagicMock, PropertyMock


class TestGetCurrentWebsocketConfig:
    """Tests for get_current_websocket_config function"""

    def test_returns_config_from_database(self):
        """Should return config when database connection exists"""
        from aird.core.websocket_manager import get_current_websocket_config
        
        mock_conn = MagicMock()
        expected_config = {'preview_max_connections': 50, 'preview_idle_timeout': 120}
        
        with patch('aird.database.db.get_db_conn', return_value=mock_conn), \
             patch('aird.database.feature_flags.load_websocket_config', return_value=expected_config):
            result = get_current_websocket_config()
            assert result == expected_config

    def test_returns_empty_dict_when_no_connection(self):
        """Should return empty dict when no database connection"""
        from aird.core.websocket_manager import get_current_websocket_config
        
        with patch('aird.database.db.get_db_conn', return_value=None):
            result = get_current_websocket_config()
            assert result == {}


class TestWebSocketConnectionManager:
    """Tests for WebSocketConnectionManager class"""

    @pytest.fixture
    def mock_ioloop(self):
        """Mock IOLoop to prevent actual timer setup"""
        mock_loop = MagicMock()
        with patch('tornado.ioloop.IOLoop.current', return_value=mock_loop):
            yield mock_loop

    @pytest.fixture
    def manager(self, mock_ioloop):
        """Create a WebSocketConnectionManager instance for testing"""
        from aird.core.websocket_manager import WebSocketConnectionManager
        return WebSocketConnectionManager(
            config_prefix='test',
            default_max_connections=10,
            default_idle_timeout=60
        )

    @pytest.fixture
    def mock_connection(self):
        """Create a mock WebSocket connection"""
        conn = MagicMock()
        conn.ws_connection = MagicMock()
        return conn

    # Initialization tests
    def test_init_sets_defaults(self, mock_ioloop):
        """Should initialize with correct default values"""
        from aird.core.websocket_manager import WebSocketConnectionManager
        
        manager = WebSocketConnectionManager(
            config_prefix='preview',
            default_max_connections=100,
            default_idle_timeout=300
        )
        
        assert manager.config_prefix == 'preview'
        assert manager.default_max_connections == 100
        assert manager.default_idle_timeout == 300
        assert len(manager.connections) == 0

    def test_init_starts_cleanup_timer(self, mock_ioloop):
        """Should start periodic cleanup timer on init"""
        from aird.core.websocket_manager import WebSocketConnectionManager
        
        WebSocketConnectionManager(
            config_prefix='test',
            default_max_connections=10,
            default_idle_timeout=60
        )
        
        # Should call call_later to schedule cleanup
        mock_ioloop.call_later.assert_called_once()
        args = mock_ioloop.call_later.call_args
        assert args[0][0] == 60  # 60 second interval

    # Property tests
    def test_max_connections_uses_config_value(self, manager):
        """Should return configured max_connections when available"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 50}):
            assert manager.max_connections == 50

    def test_max_connections_uses_default_when_not_configured(self, manager):
        """Should return default max_connections when not in config"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={}):
            assert manager.max_connections == 10  # default_max_connections

    def test_idle_timeout_uses_config_value(self, manager):
        """Should return configured idle_timeout when available"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_idle_timeout': 120}):
            assert manager.idle_timeout == 120

    def test_idle_timeout_uses_default_when_not_configured(self, manager):
        """Should return default idle_timeout when not in config"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={}):
            assert manager.idle_timeout == 60  # default_idle_timeout

    # add_connection tests
    def test_add_connection_success(self, manager, mock_connection):
        """Should add connection and return True when under limit"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10}):
            result = manager.add_connection(mock_connection)
            
            assert result is True
            assert mock_connection in manager.connections
            assert mock_connection in manager.connection_times
            assert mock_connection in manager.last_activity

    def test_add_connection_fails_at_limit(self, manager, mock_connection):
        """Should return False when at connection limit"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 1}):
            # Add first connection
            conn1 = MagicMock()
            manager.add_connection(conn1)
            
            # Try to add second connection
            result = manager.add_connection(mock_connection)
            
            assert result is False
            assert mock_connection not in manager.connections

    def test_add_connection_records_time(self, manager, mock_connection):
        """Should record connection time and activity time"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10}):
            before_time = time.time()
            manager.add_connection(mock_connection)
            after_time = time.time()
            
            conn_time = manager.connection_times[mock_connection]
            activity_time = manager.last_activity[mock_connection]
            
            assert before_time <= conn_time <= after_time
            assert before_time <= activity_time <= after_time

    # remove_connection tests
    def test_remove_connection_success(self, manager, mock_connection):
        """Should remove connection and clean up tracking"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10}):
            manager.add_connection(mock_connection)
            manager.remove_connection(mock_connection)
            
            assert mock_connection not in manager.connections

    def test_remove_connection_nonexistent(self, manager, mock_connection):
        """Should handle removing non-existent connection gracefully"""
        # Should not raise any exception
        manager.remove_connection(mock_connection)
        assert mock_connection not in manager.connections

    # update_activity tests
    def test_update_activity(self, manager, mock_connection):
        """Should update last activity time"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10}):
            manager.add_connection(mock_connection)
            original_time = manager.last_activity[mock_connection]
            
            time.sleep(0.01)  # Small delay to ensure time difference
            manager.update_activity(mock_connection)
            
            new_time = manager.last_activity[mock_connection]
            assert new_time >= original_time

    # cleanup_dead_connections tests
    # def test_cleanup_dead_connections_removes_closed(self, manager):
    #     """Should remove connections with no ws_connection"""
    #     with patch('aird.core.websocket_manager.get_current_websocket_config', 
    #                return_value={'test_max_connections': 10}):
    #         # Create a closed connection (no ws_connection)
    #         dead_conn = MagicMock()
    #         dead_conn.ws_connection = None
    #         manager.add_connection(dead_conn)
            
    #         manager.cleanup_dead_connections()
            
    #         assert dead_conn not in manager.connections

    # def test_cleanup_dead_connections_keeps_healthy(self, manager, mock_connection):
    #     """Should keep connections that respond to ping"""
    #     with patch('aird.core.websocket_manager.get_current_websocket_config', 
    #                return_value={'test_max_connections': 10}):
    #         manager.add_connection(mock_connection)
            
    #         manager.cleanup_dead_connections()
            
    #         assert mock_connection in manager.connections
    #         mock_connection.ping.assert_called()

    # def test_cleanup_dead_connections_removes_on_exception(self, manager):
    #     """Should remove connections that raise exception on ping"""
    #     with patch('aird.core.websocket_manager.get_current_websocket_config', 
    #                return_value={'test_max_connections': 10}):
    #         bad_conn = MagicMock()
    #         bad_conn.ws_connection = MagicMock()
    #         bad_conn.ping.side_effect = Exception("Connection error")
    #         manager.add_connection(bad_conn)
            
    #         manager.cleanup_dead_connections()
            
    #         assert bad_conn not in manager.connections

    # cleanup_idle_connections tests
    # def test_cleanup_idle_connections_removes_idle(self, manager, mock_connection):
    #     """Should remove connections that exceed idle timeout"""
    #     with patch('aird.core.websocket_manager.get_current_websocket_config', 
    #                return_value={'test_max_connections': 10, 'test_idle_timeout': 1}):
    #         manager.add_connection(mock_connection)
    #         # Set last_activity to a time in the past
    #         manager.last_activity[mock_connection] = time.time() - 100
            
    #         manager.cleanup_idle_connections()
            
    #         assert mock_connection not in manager.connections
    #         mock_connection.close.assert_called_with(code=1000, reason="Idle timeout")

    # def test_cleanup_idle_connections_keeps_active(self, manager, mock_connection):
    #     """Should keep connections that are not idle"""
    #     with patch('aird.core.websocket_manager.get_current_websocket_config', 
    #                return_value={'test_max_connections': 10, 'test_idle_timeout': 300}):
    #         manager.add_connection(mock_connection)
            
    #         manager.cleanup_idle_connections()
            
    #         assert mock_connection in manager.connections

    # def test_cleanup_idle_handles_close_exception(self, manager):
    #     """Should handle exception when closing idle connection"""
    #     with patch('aird.core.websocket_manager.get_current_websocket_config', 
    #                return_value={'test_max_connections': 10, 'test_idle_timeout': 1}):
    #         conn = MagicMock()
    #         conn.close.side_effect = Exception("Close error")
    #         manager.add_connection(conn)
    #         manager.last_activity[conn] = time.time() - 100
            
    #         # Should not raise exception
    #         manager.cleanup_idle_connections()
            
    #         assert conn not in manager.connections

    # get_stats tests
    def test_get_stats_empty(self, manager):
        """Should return correct stats when no connections"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10, 'test_idle_timeout': 60}):
            stats = manager.get_stats()
            
            assert stats['active_connections'] == 0
            assert stats['max_connections'] == 10
            assert stats['idle_timeout'] == 60
            assert stats['oldest_connection_age'] == 0
            assert stats['average_connection_age'] == 0

    def test_get_stats_with_connections(self, manager, mock_connection):
        """Should return correct stats with active connections"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10, 'test_idle_timeout': 60}):
            manager.add_connection(mock_connection)
            
            stats = manager.get_stats()
            
            assert stats['active_connections'] == 1
            assert stats['max_connections'] == 10
            assert stats['idle_timeout'] == 60
            assert stats['oldest_connection_age'] >= 0
            assert stats['average_connection_age'] >= 0

    def test_get_stats_multiple_connections(self, manager):
        """Should calculate correct average for multiple connections"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10, 'test_idle_timeout': 60}):
            conn1 = MagicMock()
            conn1.ws_connection = MagicMock()
            conn2 = MagicMock()
            conn2.ws_connection = MagicMock()
            
            manager.add_connection(conn1)
            time.sleep(0.01)
            manager.add_connection(conn2)
            
            stats = manager.get_stats()
            
            assert stats['active_connections'] == 2
            assert stats['oldest_connection_age'] >= stats['average_connection_age']

    # broadcast_message tests
    def test_broadcast_message_to_all(self, manager):
        """Should broadcast message to all connections"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10}):
            conn1 = MagicMock()
            conn1.ws_connection = MagicMock()
            conn2 = MagicMock()
            conn2.ws_connection = MagicMock()
            
            manager.add_connection(conn1)
            manager.add_connection(conn2)
            
            manager.broadcast_message("test message")
            
            conn1.write_message.assert_called_with("test message")
            conn2.write_message.assert_called_with("test message")

    def test_broadcast_message_with_filter(self, manager):
        """Should only broadcast to connections matching filter"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10}):
            conn1 = MagicMock()
            conn1.ws_connection = MagicMock()
            conn1.user = "user1"
            conn2 = MagicMock()
            conn2.ws_connection = MagicMock()
            conn2.user = "user2"
            
            manager.add_connection(conn1)
            manager.add_connection(conn2)
            
            # Filter to only send to user1
            manager.broadcast_message("test message", filter_func=lambda c: c.user == "user1")
            
            conn1.write_message.assert_called_with("test message")
            conn2.write_message.assert_not_called()

    # def test_broadcast_message_removes_dead_connections(self, manager):
    #     """Should remove connections that fail during broadcast"""
    #     with patch('aird.core.websocket_manager.get_current_websocket_config', 
    #                return_value={'test_max_connections': 10}):
    #         good_conn = MagicMock()
    #         good_conn.ws_connection = MagicMock()
    #         bad_conn = MagicMock()
    #         bad_conn.ws_connection = MagicMock()
    #         bad_conn.write_message.side_effect = Exception("Write error")
            
    #         manager.add_connection(good_conn)
    #         manager.add_connection(bad_conn)
            
    #         manager.broadcast_message("test message")
            
    #         assert good_conn in manager.connections
    #         assert bad_conn not in manager.connections

    def test_broadcast_message_updates_activity(self, manager, mock_connection):
        """Should update activity time for successful broadcasts"""
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 10}):
            manager.add_connection(mock_connection)
            original_time = manager.last_activity[mock_connection]
            
            time.sleep(0.01)
            manager.broadcast_message("test message")
            
            new_time = manager.last_activity[mock_connection]
            assert new_time >= original_time

    def test_broadcast_empty_connections(self, manager):
        """Should handle broadcast with no connections gracefully"""
        # Should not raise exception
        manager.broadcast_message("test message")

    # Thread safety tests
    def test_add_connection_thread_safe(self, manager):
        """Should handle concurrent add_connection calls safely"""
        import threading
        
        with patch('aird.core.websocket_manager.get_current_websocket_config', 
                   return_value={'test_max_connections': 100}):
            connections = []
            errors = []
            
            def add_conn():
                try:
                    conn = MagicMock()
                    conn.ws_connection = MagicMock()
                    manager.add_connection(conn)
                    connections.append(conn)
                except Exception as e:
                    errors.append(e)
            
            threads = [threading.Thread(target=add_conn) for _ in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            
            assert len(errors) == 0
            assert len(manager.connections) == 10

