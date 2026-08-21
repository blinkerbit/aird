//! Dedicated HTTP/1.1 upload listener — socket recv → disk without Python chunks.
//!
//! Python registers a session (ticket + file fd). The browser POSTs the body here
//! with X-Upload-* headers; we pump with recv_to_fd / pwrite.

use pyo3::exceptions::{PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::AtomicBool;
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::Duration;

use crate::{map_io_err, recv_to_fd_impl, write_at_offset_impl};

#[cfg(unix)]
use std::os::unix::io::{IntoRawFd, RawFd};

#[cfg(windows)]
use std::os::windows::io::{IntoRawSocket, RawSocket};
#[cfg(windows)]
use windows_sys::Win32::Networking::WinSock::closesocket;

struct Session {
    ticket: String,
    file_fd: i32,
    total: u64,
}

struct ServerState {
    sessions: HashMap<String, Session>,
    running: bool,
}

fn state() -> &'static Mutex<ServerState> {
    static STATE: OnceLock<Mutex<ServerState>> = OnceLock::new();
    STATE.get_or_init(|| {
        Mutex::new(ServerState {
            sessions: HashMap::new(),
            running: false,
        })
    })
}

fn header_value<'a>(headers: &'a str, name: &str) -> Option<&'a str> {
    for line in headers.lines() {
        let line = line.trim_end_matches('\r');
        if let Some((k, v)) = line.split_once(':') {
            if k.eq_ignore_ascii_case(name) {
                return Some(v.trim());
            }
        }
    }
    None
}

fn http_respond(stream: &mut TcpStream, status: &str, body: &str) {
    let resp = format!(
        "HTTP/1.1 {status}\r\n\
         Content-Type: text/plain; charset=UTF-8\r\n\
         Content-Length: {}\r\n\
         Access-Control-Allow-Origin: *\r\n\
         Access-Control-Allow-Headers: content-type,x-aird-upload-ticket,x-upload-session,x-upload-offset,x-upload-total,x-upload-filename,x-upload-dir,x-upload-complete,x-xsrftoken\r\n\
         Access-Control-Allow-Methods: POST, OPTIONS\r\n\
         Connection: close\r\n\
         \r\n\
         {body}",
        body.len()
    );
    let _ = stream.write_all(resp.as_bytes());
}

fn cors_preflight(stream: &mut TcpStream) {
    http_respond(stream, "204 No Content", "");
}

fn read_headers(stream: &mut TcpStream) -> std::io::Result<(String, Vec<u8>)> {
    let mut buf = Vec::with_capacity(8192);
    let mut tmp = [0u8; 4096];
    loop {
        let n = stream.read(&mut tmp)?;
        if n == 0 {
            break;
        }
        buf.extend_from_slice(&tmp[..n]);
        if let Some(pos) = find_header_end(&buf) {
            let header_bytes = buf[..pos].to_vec();
            let body_prefetch = buf[pos..].to_vec();
            let headers = String::from_utf8_lossy(&header_bytes).into_owned();
            return Ok((headers, body_prefetch));
        }
        if buf.len() > 64 * 1024 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::InvalidData,
                "headers too large",
            ));
        }
    }
    Err(std::io::Error::new(
        std::io::ErrorKind::UnexpectedEof,
        "connection closed before headers",
    ))
}

fn find_header_end(buf: &[u8]) -> Option<usize> {
    buf.windows(4)
        .position(|w| w == b"\r\n\r\n")
        .map(|i| i + 4)
}

fn handle_client(mut stream: TcpStream) {
    let _ = stream.set_read_timeout(Some(Duration::from_secs(600)));
    let _ = stream.set_write_timeout(Some(Duration::from_secs(60)));

    let (headers, mut prefetch) = match read_headers(&mut stream) {
        Ok(v) => v,
        Err(_) => return,
    };

    let first = headers.lines().next().unwrap_or("");
    if first.starts_with("OPTIONS ") {
        cors_preflight(&mut stream);
        return;
    }
    if !first.starts_with("POST ") {
        http_respond(&mut stream, "405 Method Not Allowed", "POST only");
        return;
    }

    let ticket = match header_value(&headers, "X-Aird-Upload-Ticket") {
        Some(t) if !t.is_empty() => t.to_string(),
        _ => {
            http_respond(&mut stream, "401 Unauthorized", "Missing upload ticket");
            return;
        }
    };
    let session_id = match header_value(&headers, "X-Upload-Session") {
        Some(s) if !s.is_empty() => s.to_string(),
        _ => {
            http_respond(&mut stream, "400 Bad Request", "Missing upload session");
            return;
        }
    };
    let offset: u64 = match header_value(&headers, "X-Upload-Offset")
        .unwrap_or("0")
        .parse()
    {
        Ok(v) => v,
        Err(_) => {
            http_respond(&mut stream, "400 Bad Request", "Bad offset");
            return;
        }
    };
    let content_len: u64 = match header_value(&headers, "Content-Length").and_then(|v| v.parse().ok())
    {
        Some(v) => v,
        None => {
            http_respond(&mut stream, "411 Length Required", "Content-Length required");
            return;
        }
    };

    let (file_fd, total) = {
        let st = state().lock().unwrap();
        match st.sessions.get(&session_id) {
            Some(sess) if sess.ticket == ticket => (sess.file_fd, sess.total),
            Some(_) => {
                drop(st);
                http_respond(&mut stream, "403 Forbidden", "Bad ticket");
                return;
            }
            None => {
                drop(st);
                http_respond(&mut stream, "404 Not Found", "Unknown session");
                return;
            }
        }
    };

    if offset.saturating_add(content_len) > total {
        http_respond(&mut stream, "400 Bad Request", "Chunk out of range");
        return;
    }

    // Drain any body bytes already read with the headers.
    if (prefetch.len() as u64) > content_len {
        prefetch.truncate(content_len as usize);
    }
    let mut written: u64 = 0;
    if !prefetch.is_empty() {
        if let Err(e) = write_at_offset_impl(file_fd, &prefetch, offset) {
            http_respond(
                &mut stream,
                "500 Internal Server Error",
                &format!("write failed: {e}"),
            );
            return;
        }
        written = prefetch.len() as u64;
    }

    let remaining = content_len - written;
    if remaining == 0 {
        http_respond(&mut stream, "200 OK", &format!("OK {content_len}"));
        return;
    }

    let cancel = AtomicBool::new(false);
    let pump_offset = offset + written;

    #[cfg(windows)]
    {
        let sock: RawSocket = stream.into_raw_socket();
        let result = recv_to_fd_impl(sock, file_fd, pump_offset, remaining, &cancel);
        match &result {
            Ok(n) if *n == remaining => {
                let body = format!("OK {content_len}");
                let resp = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=UTF-8\r\n\
                     Content-Length: {}\r\nAccess-Control-Allow-Origin: *\r\n\
                     Connection: close\r\n\r\n{body}",
                    body.len()
                );
                let _ = crate::socket_send_all_impl(sock, resp.as_bytes());
            }
            Ok(_) => {
                let msg = "short read";
                let resp = format!(
                    "HTTP/1.1 499 Client Closed Request\r\nContent-Length: {}\r\n\
                     Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n{msg}",
                    msg.len()
                );
                let _ = crate::socket_send_all_impl(sock, resp.as_bytes());
            }
            Err(e) => {
                let msg = format!("pump failed: {e}");
                let resp = format!(
                    "HTTP/1.1 500 Internal Server Error\r\nContent-Length: {}\r\n\
                     Access-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n{msg}",
                    msg.len()
                );
                let _ = crate::socket_send_all_impl(sock, resp.as_bytes());
            }
        }
        unsafe {
            let _ = closesocket(sock as _);
        }
        return;
    }

    #[cfg(unix)]
    {
        let fd: RawFd = stream.into_raw_fd();
        let result = recv_to_fd_impl(fd, file_fd, pump_offset, remaining, &cancel);
        use std::os::unix::io::FromRawFd;
        // Safety: fd came from into_raw_fd above.
        let mut stream = unsafe { TcpStream::from_raw_fd(fd) };
        match result {
            Ok(n) if n == remaining => {
                http_respond(&mut stream, "200 OK", &format!("OK {content_len}"));
            }
            Ok(_) => http_respond(&mut stream, "499 Client Closed Request", "short read"),
            Err(e) => http_respond(
                &mut stream,
                "500 Internal Server Error",
                &format!("pump failed: {e}"),
            ),
        }
    }
}

fn accept_loop(listener: TcpListener) {
    for conn in listener.incoming() {
        match conn {
            Ok(stream) => {
                thread::spawn(move || handle_client(stream));
            }
            Err(_) => continue,
        }
    }
}

#[pyfunction]
fn register_upload_session(session_id: &str, ticket: &str, file_fd: i32, total: u64) -> PyResult<()> {
    if session_id.is_empty() || ticket.is_empty() || file_fd < 0 || total == 0 {
        return Err(PyValueError::new_err("invalid upload session args"));
    }
    let mut st = state().lock().unwrap();
    st.sessions.insert(
        session_id.to_string(),
        Session {
            ticket: ticket.to_string(),
            file_fd,
            total,
        },
    );
    Ok(())
}

#[pyfunction]
fn unregister_upload_session(session_id: &str) -> PyResult<()> {
    let mut st = state().lock().unwrap();
    st.sessions.remove(session_id);
    Ok(())
}

#[pyfunction]
fn start_upload_http_server(host: &str, port: u16) -> PyResult<u16> {
    if port == 0 {
        return Err(PyValueError::new_err("port must be > 0"));
    }
    {
        let mut st = state().lock().unwrap();
        if st.running {
            return Ok(port);
        }
        st.running = true;
    }
    let addr = format!("{host}:{port}");
    let listener = TcpListener::bind(&addr).map_err(map_io_err)?;
    let bound = listener.local_addr().map_err(map_io_err)?.port();
    thread::Builder::new()
        .name("aird-upload-http".into())
        .spawn(move || accept_loop(listener))
        .map_err(|e| PyRuntimeError::new_err(e.to_string()))?;
    Ok(bound)
}

#[pyfunction]
fn upload_http_server_running() -> bool {
    state().lock().unwrap().running
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(register_upload_session, m)?)?;
    m.add_function(wrap_pyfunction!(unregister_upload_session, m)?)?;
    m.add_function(wrap_pyfunction!(start_upload_http_server, m)?)?;
    m.add_function(wrap_pyfunction!(upload_http_server_running, m)?)?;
    Ok(())
}
