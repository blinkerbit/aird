"""
QUIC/HTTP3 Server implementation for Aird using aioquic
"""

import asyncio
import logging
import os
import ssl
from typing import Optional, Dict, Any
from urllib.parse import unquote

try:
    from aioquic.asyncio import serve
    from aioquic.asyncio.protocol import QuicConnectionProtocol
    from aioquic.h3.connection import H3_ALPN, H3Connection
    from aioquic.h3.events import (
        DataReceived,
        H3Event,
        HeadersReceived,
        StreamReset,
    )
    from aioquic.quic.configuration import QuicConfiguration
    from aioquic.quic.events import QuicEvent
    from aioquic.tls import SessionTicket
    AIOQUIC_AVAILABLE = True
except ImportError:
    AIOQUIC_AVAILABLE = False
    # Create dummy classes for type hints when aioquic is not available
    class QuicConnectionProtocol:
        pass
    class H3Connection:
        pass

logger = logging.getLogger(__name__)


class AirdQuicHandler(QuicConnectionProtocol):
    """
    QUIC connection handler that bridges HTTP/3 requests to Aird's existing handlers
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.http: Optional[H3Connection] = None
        self.tornado_app: Optional[Any] = None
        self.streams: Dict[int, Dict[str, Any]] = {}

    def quic_event_received(self, event: QuicEvent) -> None:
        """Handle QUIC events"""
        if self.http is not None:
            for http_event in self.http.handle_event(event):
                self.http_event_received(http_event)

    def http_event_received(self, event: H3Event) -> None:
        """Handle HTTP/3 events"""
        if isinstance(event, HeadersReceived):
            asyncio.create_task(self.handle_request(event))
        elif isinstance(event, DataReceived):
            # Handle request body data
            if event.stream_id in self.streams:
                stream = self.streams[event.stream_id]
                if 'body' not in stream:
                    stream['body'] = b''
                stream['body'] += event.data
        elif isinstance(event, StreamReset):
            # Clean up stream
            self.streams.pop(event.stream_id, None)

    async def handle_request(self, event: HeadersReceived):
        """Handle HTTP/3 request by converting to Tornado format"""
        stream_id = event.stream_id
        
        # Parse headers
        headers = {}
        method = 'GET'
        path = '/'
        
        for name, value in event.headers:
            name_str = name.decode('utf-8')
            value_str = value.decode('utf-8')
            
            if name_str == ':method':
                method = value_str
            elif name_str == ':path':
                path = unquote(value_str)
            elif not name_str.startswith(':'):
                headers[name_str] = value_str

        # Store stream info
        self.streams[stream_id] = {
            'method': method,
            'path': path,
            'headers': headers,
            'response_sent': False
        }

        try:
            # Handle the request based on path
            await self.route_request(stream_id, method, path, headers)
        except Exception as e:
            logger.error(f"Error handling QUIC request: {e}")
            await self.send_error_response(stream_id, 500, str(e))

    async def route_request(self, stream_id: int, method: str, path: str, headers: Dict[str, str]):
        """Route the request to appropriate handler"""
        try:
            # Simple routing logic - expand this based on Aird's actual routes
            if path.startswith('/files/'):
                await self.handle_file_request(stream_id, method, path, headers)
            elif path == '/':
                await self.handle_root_request(stream_id)
            elif path.startswith('/static/') or path.startswith('/css/') or path.startswith('/js/'):
                await self.handle_static_request(stream_id, path)
            else:
                # For now, redirect complex requests to the main HTTP server
                await self.send_redirect_response(stream_id, f"http://localhost:8000{path}")
        except Exception as e:
            logger.error(f"Error routing QUIC request: {e}")
            await self.send_error_response(stream_id, 500, str(e))

    async def handle_file_request(self, stream_id: int, method: str, path: str, headers: Dict[str, str]):
        """Handle file requests over QUIC"""
        try:
            # Extract file path
            file_path = path[7:]  # Remove '/files/' prefix
            
            # Basic file serving - this should integrate with Aird's existing file handling
            response_headers = [
                (b":status", b"200"),
                (b"content-type", b"text/plain"),
                (b"server", b"Aird-QUIC/0.4.0"),
            ]
            
            response_body = f"QUIC file request: {file_path}\nThis is a placeholder response.".encode('utf-8')
            
            # Send headers
            self.http.send_headers(stream_id=stream_id, headers=response_headers)
            
            # Send body
            self.http.send_data(stream_id=stream_id, data=response_body, end_stream=True)
            
            self.streams[stream_id]['response_sent'] = True
            
        except Exception as e:
            await self.send_error_response(stream_id, 500, str(e))

    async def handle_root_request(self, stream_id: int):
        """Handle root request"""
        response_headers = [
            (b":status", b"200"),
            (b"content-type", b"text/html"),
            (b"server", b"Aird-QUIC/0.4.0"),
        ]
        
        response_body = """
        <!DOCTYPE html>
        <html>
        <head>
            <title>Aird - QUIC Enabled</title>
        </head>
        <body>
            <h1>🚀 Aird with QUIC Protocol Support</h1>
            <p>You are connected via HTTP/3 over QUIC!</p>
            <p><a href="/files/">Browse Files</a></p>
            <p><em>This is served over the QUIC protocol for enhanced performance.</em></p>
        </body>
        </html>
        """.encode('utf-8')
        
        self.http.send_headers(stream_id=stream_id, headers=response_headers)
        self.http.send_data(stream_id=stream_id, data=response_body, end_stream=True)
        self.streams[stream_id]['response_sent'] = True

    async def handle_static_request(self, stream_id: int, path: str):
        """Handle static file requests"""
        # For now, return a simple response
        response_headers = [
            (b":status", b"404"),
            (b"content-type", b"text/plain"),
            (b"server", b"Aird-QUIC/0.4.0"),
        ]
        
        response_body = f"Static file not found: {path}".encode('utf-8')
        
        self.http.send_headers(stream_id=stream_id, headers=response_headers)
        self.http.send_data(stream_id=stream_id, data=response_body, end_stream=True)
        self.streams[stream_id]['response_sent'] = True

    async def send_error_response(self, stream_id: int, status: int, message: str):
        """Send error response"""
        if self.streams.get(stream_id, {}).get('response_sent'):
            return
            
        response_headers = [
            (b":status", str(status).encode()),
            (b"content-type", b"text/plain"),
            (b"server", b"Aird-QUIC/0.4.0"),
        ]
        
        self.http.send_headers(stream_id=stream_id, headers=response_headers)
        self.http.send_data(stream_id=stream_id, data=message.encode('utf-8'), end_stream=True)
        self.streams[stream_id]['response_sent'] = True

    async def send_redirect_response(self, stream_id: int, location: str):
        """Send redirect response"""
        response_headers = [
            (b":status", b"302"),
            (b"location", location.encode('utf-8')),
            (b"server", b"Aird-QUIC/0.4.0"),
        ]
        
        self.http.send_headers(stream_id=stream_id, headers=response_headers)
        self.http.send_data(stream_id=stream_id, data=b"", end_stream=True)
        self.streams[stream_id]['response_sent'] = True


async def create_quic_server(
    host: str = "0.0.0.0",
    port: int = 4433,
    cert_file: Optional[str] = None,
    key_file: Optional[str] = None,
    tornado_app: Optional[Any] = None
) -> Optional[Any]:
    """
    Create and start a QUIC server
    
    Args:
        host: Host to bind to
        port: Port to bind to
        cert_file: Path to SSL certificate file
        key_file: Path to SSL private key file
        tornado_app: Tornado application instance for routing
        
    Returns:
        Server instance or None if aioquic is not available
    """
    if not AIOQUIC_AVAILABLE:
        logger.warning("aioquic not available - QUIC support disabled")
        return None
    
    try:
        # Create QUIC configuration
        configuration = QuicConfiguration(
            alpn_protocols=H3_ALPN,
            is_client=False,
            max_datagram_frame_size=65536,
        )
        
        # Set up SSL context
        if cert_file and key_file and os.path.exists(cert_file) and os.path.exists(key_file):
            configuration.load_cert_chain(cert_file, key_file)
        else:
            # Generate self-signed certificate for development
            logger.info("Generating self-signed certificate for QUIC")
            cert_file, key_file = await generate_self_signed_cert()
            if cert_file and key_file:
                configuration.load_cert_chain(cert_file, key_file)
            else:
                logger.error("Failed to generate SSL certificate for QUIC")
                return None

        # Create server
        def create_protocol():
            protocol = AirdQuicHandler(configuration)
            protocol.http = H3Connection(protocol, enable_webtransport=False)
            protocol.tornado_app = tornado_app
            return protocol

        server = await serve(
            host,
            port,
            configuration=configuration,
            create_protocol=create_protocol,
        )
        
        logger.info(f"QUIC server started on {host}:{port}")
        return server
        
    except Exception as e:
        logger.error(f"Failed to start QUIC server: {e}")
        return None


async def generate_self_signed_cert():
    """Generate a self-signed certificate for development"""
    try:
        import tempfile
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
        import datetime
        
        # Generate private key
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        
        # Generate certificate
        subject = issuer = x509.Name([
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "Development"),
            x509.NameAttribute(NameOID.LOCALITY_NAME, "Development"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Aird Development"),
            x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
        ])
        
        cert = x509.CertificateBuilder().subject_name(
            subject
        ).issuer_name(
            issuer
        ).public_key(
            private_key.public_key()
        ).serial_number(
            x509.random_serial_number()
        ).not_valid_before(
            datetime.datetime.utcnow()
        ).not_valid_after(
            datetime.datetime.utcnow() + datetime.timedelta(days=365)
        ).add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress("127.0.0.1"),
            ]),
            critical=False,
        ).sign(private_key, hashes.SHA256())
        
        # Write to temporary files
        cert_fd, cert_path = tempfile.mkstemp(suffix='.crt', prefix='aird_quic_')
        key_fd, key_path = tempfile.mkstemp(suffix='.key', prefix='aird_quic_')
        
        with os.fdopen(cert_fd, 'wb') as f:
            f.write(cert.public_bytes(serialization.Encoding.PEM))
        
        with os.fdopen(key_fd, 'wb') as f:
            f.write(private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption()
            ))
        
        logger.info(f"Generated self-signed certificate: {cert_path}")
        logger.info(f"Generated private key: {key_path}")
        
        return cert_path, key_path
        
    except ImportError:
        logger.error("cryptography library not available for certificate generation")
        return None, None
    except Exception as e:
        logger.error(f"Failed to generate self-signed certificate: {e}")
        return None, None


def is_quic_available() -> bool:
    """Check if QUIC support is available"""
    return AIOQUIC_AVAILABLE
