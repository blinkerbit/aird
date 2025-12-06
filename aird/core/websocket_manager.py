"""WebSocket connection manager with memory leak prevention."""

from typing import Set
import time
import threading
import weakref
import tornado.ioloop


def get_current_websocket_config():
    """Get current websocket configuration from database or defaults."""
    from aird.database.db import get_db_conn
    from aird.database.feature_flags import load_websocket_config
    
    conn = get_db_conn()
    if conn:
        return load_websocket_config(conn)
    return {}


class WebSocketConnectionManager:
    """Base class for managing WebSocket connections with memory leak prevention"""
    
    def __init__(self, config_prefix: str, default_max_connections: int = 100, default_idle_timeout: int = 300):
        self.connections: Set = set()
        self.config_prefix = config_prefix
        self.default_max_connections = default_max_connections
        self.default_idle_timeout = default_idle_timeout
        self.connection_times = weakref.WeakKeyDictionary()
        self.last_activity = weakref.WeakKeyDictionary()
        self._cleanup_lock = threading.Lock()
        
        # Start periodic cleanup
        self._setup_cleanup_timer()
    
    @property
    def max_connections(self) -> int:
        """Get current max connections from configuration"""
        config = get_current_websocket_config()
        return config.get(f"{self.config_prefix}_max_connections", self.default_max_connections)
    
    @property
    def idle_timeout(self) -> int:
        """Get current idle timeout from configuration"""
        config = get_current_websocket_config()
        return config.get(f"{self.config_prefix}_idle_timeout", self.default_idle_timeout)
    
    def _setup_cleanup_timer(self):
        """Setup periodic cleanup of dead and idle connections"""
        def cleanup():
            self.cleanup_dead_connections()
            self.cleanup_idle_connections()
            # Schedule next cleanup
            tornado.ioloop.IOLoop.current().call_later(60, cleanup)
        
        # Start cleanup in 60 seconds
        tornado.ioloop.IOLoop.current().call_later(60, cleanup)
    
    def add_connection(self, connection) -> bool:
        """Add a connection if under limit. Returns True if added."""
        with self._cleanup_lock:
            if len(self.connections) >= self.max_connections:
                return False
            
            self.connections.add(connection)
            self.connection_times[connection] = time.time()
            self.last_activity[connection] = time.time()
            return True
    
    def remove_connection(self, connection):
        """Remove a connection safely"""
        with self._cleanup_lock:
            self.connections.discard(connection)
            self.connection_times.pop(connection, None)
            self.last_activity.pop(connection, None)
    
    def update_activity(self, connection):
        """Update last activity time for a connection"""
        self.last_activity[connection] = time.time()
    
    def cleanup_dead_connections(self):
        """Remove connections that can't receive messages"""
        with self._cleanup_lock:
            dead_connections = set()
            for conn in list(self.connections):
                try:
                    # Try to ping the connection
                    if hasattr(conn, 'ws_connection') and conn.ws_connection:
                        conn.ping()
                    else:
                        # Connection is closed
                        dead_connections.add(conn)
                except Exception:
                    dead_connections.add(conn)
            
            for conn in dead_connections:
                self.remove_connection(conn)
    
    def cleanup_idle_connections(self):
        """Remove connections that have been idle too long"""
        with self._cleanup_lock:
            current_time = time.time()
            idle_connections = set()
            
            for conn in list(self.connections):
                last_activity = self.last_activity.get(conn, 0)
                if current_time - last_activity > self.idle_timeout:
                    idle_connections.add(conn)
            
            for conn in idle_connections:
                try:
                    if hasattr(conn, 'close'):
                        conn.close(code=1000, reason="Idle timeout")
                except Exception:
                    pass
                self.remove_connection(conn)
    
    def get_stats(self) -> dict:
        """Get connection statistics"""
        with self._cleanup_lock:
            current_time = time.time()
            return {
                'active_connections': len(self.connections),
                'max_connections': self.max_connections,
                'idle_timeout': self.idle_timeout,
                'oldest_connection_age': max(
                    (current_time - self.connection_times.get(conn, current_time) 
                     for conn in self.connections), 
                    default=0
                ),
                'average_connection_age': sum(
                    current_time - self.connection_times.get(conn, current_time) 
                    for conn in self.connections
                ) / len(self.connections) if self.connections else 0
            }
    
    def broadcast_message(self, message, filter_func=None):
        """Broadcast message to all connections with optional filtering"""
        with self._cleanup_lock:
            dead_connections = set()
            for conn in list(self.connections):
                try:
                    if filter_func is None or filter_func(conn):
                        if hasattr(conn, 'write_message'):
                            conn.write_message(message)
                        self.update_activity(conn)
                except Exception:
                    dead_connections.add(conn)
            
            # Remove dead connections
            for conn in dead_connections:
                self.remove_connection(conn)
