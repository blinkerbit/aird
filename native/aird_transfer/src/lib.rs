//! Native fd pumps for AIRD HTTP uploads (used from Tornado handlers).
//!
//! Reads directly from a socket fd into a file fd without Python per-chunk copies.
//! Linux uses splice when possible; Windows uses large WSARecv/WriteFile and
//! TransmitFile for downloads (kernel cache → socket).

mod http_upload;

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, Ordering};

const BUF_SIZE: usize = 16 * 1024 * 1024;

#[cfg(unix)]
use std::os::unix::io::RawFd;

#[cfg(windows)]
use std::fs::OpenOptions;
#[cfg(windows)]
use std::mem::zeroed;
#[cfg(windows)]
use std::os::windows::fs::OpenOptionsExt;
#[cfg(windows)]
use std::os::windows::io::{AsRawHandle, RawSocket};
#[cfg(windows)]
use windows_sys::Win32::Foundation::{HANDLE, INVALID_HANDLE_VALUE};
#[cfg(windows)]
use windows_sys::Win32::Networking::WinSock::{
    closesocket, ioctlsocket, send, setsockopt, TransmitFile, WSADuplicateSocketW,
    WSAGetLastError, WSASocketW, FIONBIO, FROM_PROTOCOL_INFO, INVALID_SOCKET, IPPROTO_TCP,
    SOCKET, SOCKET_ERROR, SOL_SOCKET, SO_RCVBUF, SO_SNDBUF, TCP_NODELAY, WSAPROTOCOL_INFOW,
};
#[cfg(windows)]
use windows_sys::Win32::Storage::FileSystem::{
    SetFilePointerEx, WriteFile, FILE_BEGIN,
};
#[cfg(windows)]
use windows_sys::Win32::System::IO::CancelIoEx;

#[cfg(windows)]
#[link(name = "kernel32")]
extern "system" {
    fn GetCurrentProcessId() -> u32;
}

/// Duplicate SOCKET onto a fresh handle not associated with Tornado's IOCP.
/// Closes *old* sock. Returns the new SOCKET for exclusive overlapped/blocking I/O.
#[cfg(windows)]
fn escape_iocp_socket(sock: SOCKET) -> Result<SOCKET, std::io::Error> {
    unsafe {
        let _ = CancelIoEx(sock as HANDLE, std::ptr::null());
    }
    let mut info: WSAPROTOCOL_INFOW = unsafe { zeroed() };
    let rc = unsafe { WSADuplicateSocketW(sock, GetCurrentProcessId(), &mut info) };
    if rc != 0 {
        return Err(std::io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
    }
    let new_sock = unsafe {
        WSASocketW(
            FROM_PROTOCOL_INFO,
            FROM_PROTOCOL_INFO,
            FROM_PROTOCOL_INFO,
            &info,
            0,
            0, // no WSA_FLAG_OVERLAPPED — blocking twin
        )
    };
    if new_sock == INVALID_SOCKET {
        return Err(std::io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
    }
    // Ensure blocking mode.
    let mut nonblock: u32 = 0;
    unsafe {
        let _ = ioctlsocket(new_sock, FIONBIO, &mut nonblock);
        let _ = closesocket(sock);
    }
    Ok(new_sock)
}

/// Shared cancel flag for in-flight native uploads.
#[pyclass]
struct CancelFlag {
    inner: AtomicBool,
}

#[pymethods]
impl CancelFlag {
    #[new]
    fn new() -> Self {
        Self {
            inner: AtomicBool::new(false),
        }
    }

    fn cancel(&self) {
        self.inner.store(true, Ordering::Relaxed);
    }

    fn is_cancelled(&self) -> bool {
        self.inner.load(Ordering::Relaxed)
    }
}

pub(crate) fn map_io_err(err: std::io::Error) -> PyErr {
    PyOSError::new_err(err.to_string())
}

/// Write bytes at an absolute file offset (used by HTTP upload prefetch).
pub(crate) fn write_at_offset_impl(fd: i32, data: &[u8], offset: u64) -> Result<(), std::io::Error> {
    if data.is_empty() {
        return Ok(());
    }
    #[cfg(unix)]
    {
        pwrite_all(fd, data, offset)
    }
    #[cfg(windows)]
    {
        let mut off = 0usize;
        let mut pos = offset;
        while off < data.len() {
            let n = unsafe { libc::lseek(fd, pos as libc::off_t, 0) };
            if n < 0 {
                return Err(std::io::Error::last_os_error());
            }
            let wrote = write_fd_impl(fd, &data[off..])?;
            off += wrote;
            pos += wrote as u64;
        }
        Ok(())
    }
}

/// CRT file descriptor write (Unix fd and Windows _open/_write fds from Python os.open).
fn write_fd_impl(fd: i32, data: &[u8]) -> Result<usize, std::io::Error> {
    let mut off = 0;
    while off < data.len() {
        let count = data.len() - off;
        let n = {
            #[cfg(unix)]
            {
                unsafe {
                    libc::write(fd, data[off..].as_ptr() as *const libc::c_void, count)
                }
            }
            #[cfg(windows)]
            {
                unsafe {
                    libc::write(
                        fd,
                        data[off..].as_ptr() as *const libc::c_void,
                        count as libc::c_uint,
                    )
                }
            }
        };
        if n < 0 {
            return Err(std::io::Error::last_os_error());
        }
        if n == 0 {
            break;
        }
        off += n as usize;
    }
    Ok(off)
}

#[pyfunction]
fn write_fd(fd: i32, data: &[u8]) -> PyResult<usize> {
    if data.is_empty() {
        return Ok(0);
    }
    write_fd_impl(fd, data).map_err(map_io_err)
}

#[cfg(unix)]
fn pwrite_all(fd: RawFd, data: &[u8], mut offset: u64) -> Result<(), std::io::Error> {
    let mut off = 0usize;
    while off < data.len() {
        let n = unsafe {
            libc::pwrite(
                fd,
                data[off..].as_ptr() as *const libc::c_void,
                data.len() - off,
                offset as libc::off_t,
            )
        };
        if n < 0 {
            return Err(std::io::Error::last_os_error());
        }
        if n == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::WriteZero,
                "pwrite returned 0",
            ));
        }
        off += n as usize;
        offset += n as u64;
    }
    Ok(())
}

#[cfg(unix)]
pub(crate) fn recv_to_fd_impl(
    sock_fd: RawFd,
    file_fd: RawFd,
    file_offset: u64,
    max_bytes: u64,
    cancel: &AtomicBool,
) -> Result<u64, std::io::Error> {
    #[cfg(target_os = "linux")]
    {
        match splice_to_fd_impl(sock_fd, file_fd, file_offset, max_bytes, cancel) {
            Ok(n) => return Ok(n),
            Err(e) if matches!(
                e.raw_os_error(),
                Some(libc::EINVAL) | Some(libc::EOPNOTSUPP) | Some(libc::EAGAIN)
            ) => {}
            Err(e) => return Err(e),
        }
    }
    recv_pread_to_fd_impl(sock_fd, file_fd, file_offset, max_bytes, cancel)
}

/// Socketify-style recv(2) + pwrite(2) — one userspace buffer, no Python objects.
#[cfg(unix)]
fn recv_pread_to_fd_impl(
    sock_fd: RawFd,
    file_fd: RawFd,
    file_offset: u64,
    max_bytes: u64,
    cancel: &AtomicBool,
) -> Result<u64, std::io::Error> {
    let mut buf = vec![0u8; BUF_SIZE];
    let mut total: u64 = 0;
    let mut write_offset = file_offset;
    let mut use_read = false;

    while total < max_bytes {
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        let want = (max_bytes - total).min(BUF_SIZE as u64) as usize;
        let n = if use_read {
            unsafe {
                libc::read(
                    sock_fd,
                    buf.as_mut_ptr() as *mut libc::c_void,
                    want,
                )
            }
        } else {
            let n = unsafe {
                libc::recv(
                    sock_fd,
                    buf.as_mut_ptr() as *mut libc::c_void,
                    want,
                    0,
                )
            };
            if n < 0 {
                let err = std::io::Error::last_os_error();
                if err.raw_os_error() == Some(libc::ENOTSOCK) {
                    use_read = true;
                    continue;
                }
            }
            n
        };
        if n < 0 {
            let err = std::io::Error::last_os_error();
            match err.kind() {
                std::io::ErrorKind::Interrupted => continue,
                std::io::ErrorKind::WouldBlock => {
                    let mut pfd = libc::pollfd {
                        fd: sock_fd,
                        events: libc::POLLIN,
                        revents: 0,
                    };
                    let ret = unsafe { libc::poll(&mut pfd, 1, -1) };
                    if ret < 0 {
                        return Err(std::io::Error::last_os_error());
                    }
                    continue;
                }
                _ => return Err(err),
            }
        }
        if n == 0 {
            break;
        }
        pwrite_all(file_fd, &buf[..n as usize], write_offset)?;
        write_offset += n as u64;
        total += n as u64;
    }
    Ok(total)
}

/// Linux kernel pipe splice: socket → pipe → file (no userspace copy).
#[cfg(target_os = "linux")]
fn splice_to_fd_impl(
    sock_fd: RawFd,
    file_fd: RawFd,
    mut file_offset: u64,
    max_bytes: u64,
    cancel: &AtomicBool,
) -> Result<u64, std::io::Error> {
    let mut pipefd = [0i32; 2];
    if unsafe { libc::pipe2(pipefd.as_mut_ptr(), libc::O_CLOEXEC) } < 0 {
        return Err(std::io::Error::last_os_error());
    }
    let (pipe_r, pipe_w) = (pipefd[0], pipefd[1]);
    let mut total: u64 = 0;

    while total < max_bytes {
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        let chunk = (max_bytes - total).min(1024 * 1024) as usize;
        let n = unsafe {
            libc::splice(
                sock_fd,
                std::ptr::null_mut(),
                pipe_w,
                std::ptr::null_mut(),
                chunk,
                0,
            )
        };
        if n == 0 {
            break;
        }
        if n < 0 {
            let err = std::io::Error::last_os_error();
            let errno = err.raw_os_error();
            if errno == Some(libc::EAGAIN) || errno == Some(libc::EWOULDBLOCK) {
                let mut pfd = libc::pollfd {
                    fd: sock_fd,
                    events: libc::POLLIN,
                    revents: 0,
                };
                let ret = unsafe { libc::poll(&mut pfd, 1, -1) };
                if ret < 0 {
                    unsafe {
                        libc::close(pipe_r);
                        libc::close(pipe_w);
                    }
                    return Err(std::io::Error::last_os_error());
                }
                continue;
            }
            unsafe {
                libc::close(pipe_r);
                libc::close(pipe_w);
            }
            return Err(err);
        }
        let mut pipe_left = n as usize;
        while pipe_left > 0 {
            if file_offset > 0 {
                let seek = unsafe {
                    libc::lseek(file_fd, file_offset as libc::off_t, libc::SEEK_SET)
                };
                if seek < 0 {
                    unsafe {
                        libc::close(pipe_r);
                        libc::close(pipe_w);
                    }
                    return Err(std::io::Error::last_os_error());
                }
            }
            let m = unsafe {
                libc::splice(
                    pipe_r,
                    std::ptr::null_mut(),
                    file_fd,
                    std::ptr::null_mut(),
                    pipe_left,
                    0,
                )
            };
            if m <= 0 {
                unsafe {
                    libc::close(pipe_r);
                    libc::close(pipe_w);
                }
                return Err(std::io::Error::last_os_error());
            }
            pipe_left -= m as usize;
            file_offset += m as u64;
            total += m as u64;
        }
    }
    unsafe {
        libc::close(pipe_r);
        libc::close(pipe_w);
    }
    Ok(total)
}

#[cfg(windows)]
fn tune_socket_buffers(sock: SOCKET) {
    let buf: i32 = 16 * 1024 * 1024;
    let nodelay: i32 = 1;
    unsafe {
        let _ = setsockopt(
            sock,
            SOL_SOCKET,
            SO_RCVBUF,
            &buf as *const i32 as *const u8,
            std::mem::size_of::<i32>() as i32,
        );
        let _ = setsockopt(
            sock,
            SOL_SOCKET,
            SO_SNDBUF,
            &buf as *const i32 as *const u8,
            std::mem::size_of::<i32>() as i32,
        );
        let _ = setsockopt(
            sock,
            IPPROTO_TCP as i32,
            TCP_NODELAY as i32,
            &nodelay as *const i32 as *const u8,
            std::mem::size_of::<i32>() as i32,
        );
    }
}

/// Escape Tornado IOCP, then blocking recv → WriteFile (16 MiB buffers).
/// `sock_fd` must already be the post-`take_socket_from_iocp` handle.
#[cfg(windows)]
pub(crate) fn recv_to_fd_impl(
    sock_fd: RawSocket,
    file_fd: i32,
    file_offset: u64,
    max_bytes: u64,
    cancel: &AtomicBool,
) -> Result<u64, std::io::Error> {
    let sock = sock_fd as SOCKET;
    if sock == INVALID_SOCKET || sock == 0 {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "invalid SOCKET",
        ));
    }
    tune_socket_buffers(sock);

    let file_handle = unsafe { libc::get_osfhandle(file_fd) } as HANDLE;
    if file_handle == INVALID_HANDLE_VALUE || file_handle.is_null() {
        return Err(std::io::Error::new(
            std::io::ErrorKind::InvalidInput,
            "invalid file handle from CRT fd",
        ));
    }

    let mut buf = vec![0u8; BUF_SIZE];
    let mut total: u64 = 0;
    let mut write_offset = file_offset;

    while total < max_bytes {
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        let want = (max_bytes - total).min(BUF_SIZE as u64) as i32;
        let n = unsafe {
            windows_sys::Win32::Networking::WinSock::recv(sock, buf.as_mut_ptr(), want, 0)
        };
        if n == SOCKET_ERROR {
            return Err(std::io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
        }
        if n == 0 {
            break;
        }
        let n = n as usize;

        if write_offset > 0 {
            let mut new_pos: i64 = 0;
            let seek_ok = unsafe {
                SetFilePointerEx(file_handle, write_offset as i64, &mut new_pos, FILE_BEGIN)
            };
            if seek_ok == 0 {
                return Err(std::io::Error::last_os_error());
            }
        }

        let mut written: u32 = 0;
        let mut off = 0usize;
        while off < n {
            let ok = unsafe {
                WriteFile(
                    file_handle,
                    buf[off..].as_ptr(),
                    (n - off) as u32,
                    &mut written,
                    std::ptr::null_mut(),
                )
            };
            if ok == 0 || written == 0 {
                return Err(std::io::Error::last_os_error());
            }
            off += written as usize;
        }

        write_offset += n as u64;
        total += n as u64;
    }
    Ok(total)
}

#[pyfunction]
fn take_socket_from_iocp(sock_fd: i64) -> PyResult<i64> {
    #[cfg(not(windows))]
    {
        Ok(sock_fd)
    }
    #[cfg(windows)]
    {
        let new_sock = escape_iocp_socket(sock_fd as SOCKET).map_err(map_io_err)?;
        Ok(new_sock as i64)
    }
}

/// Send bytes on a raw SOCKET (HTTP response after exclusive upload pump).
#[cfg(windows)]
pub(crate) fn socket_send_all_impl(sock_fd: RawSocket, data: &[u8]) -> Result<(), std::io::Error> {
    let sock = sock_fd as SOCKET;
    let mut off = 0usize;
    while off < data.len() {
        let n = unsafe {
            send(
                sock,
                data[off..].as_ptr(),
                (data.len() - off) as i32,
                0,
            )
        };
        if n == SOCKET_ERROR {
            return Err(std::io::Error::from_raw_os_error(unsafe { WSAGetLastError() }));
        }
        if n == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::WriteZero,
                "send returned 0",
            ));
        }
        off += n as usize;
    }
    Ok(())
}

#[cfg(windows)]
fn socket_close_impl(sock_fd: RawSocket) {
    unsafe {
        let _ = closesocket(sock_fd as SOCKET);
    }
}

/// Kernel TransmitFile: file cache → socket (Windows download zcopy).
#[cfg(windows)]
fn transmit_file_impl(
    sock_fd: RawSocket,
    file_path: &str,
    start: u64,
    count: u64,
) -> Result<u64, std::io::Error> {
    if count == 0 {
        return Ok(0);
    }
    tune_socket_buffers(sock_fd as SOCKET);

    let file = OpenOptions::new()
        .read(true)
        .share_mode(1) // FILE_SHARE_READ
        .open(file_path)?;
    let handle = file.as_raw_handle() as HANDLE;
    if handle == INVALID_HANDLE_VALUE || handle.is_null() {
        return Err(std::io::Error::last_os_error());
    }

    let mut new_pos: i64 = 0;
    let seek_ok = unsafe { SetFilePointerEx(handle, start as i64, &mut new_pos, FILE_BEGIN) };
    if seek_ok == 0 {
        return Err(std::io::Error::last_os_error());
    }

    // TransmitFile is limited to 2 GiB-1 per call; loop for larger files.
    let mut remaining = count;
    let mut sent_total: u64 = 0;
    while remaining > 0 {
        let chunk = remaining.min(0x7FFF_FFFF);
        let ok = unsafe {
            TransmitFile(
                sock_fd as SOCKET,
                handle,
                chunk as u32,
                0,
                std::ptr::null_mut(),
                std::ptr::null_mut(),
                0,
            )
        };
        if ok == 0 {
            return Err(std::io::Error::last_os_error());
        }
        sent_total += chunk;
        remaining -= chunk;
        if remaining > 0 {
            let mut pos: i64 = 0;
            let seek_ok = unsafe {
                SetFilePointerEx(
                    handle,
                    (start + sent_total) as i64,
                    &mut pos,
                    FILE_BEGIN,
                )
            };
            if seek_ok == 0 {
                return Err(std::io::Error::last_os_error());
            }
        }
    }
    // File handle closed when `file` drops; do not CloseHandle(handle).
    Ok(sent_total)
}

#[pyfunction]
#[pyo3(signature = (fd, data, offset=0))]
fn pwrite_fd(fd: i32, data: &[u8], offset: u64) -> PyResult<usize> {
    if data.is_empty() {
        return Ok(0);
    }
    #[cfg(unix)]
    {
        pwrite_all(fd, data, offset).map_err(map_io_err)?;
        Ok(data.len())
    }
    #[cfg(windows)]
    {
        let mut off = 0usize;
        let mut pos = offset;
        while off < data.len() {
            let n = unsafe { libc::lseek(fd, pos as libc::off_t, 0) };
            if n < 0 {
                return Err(map_io_err(std::io::Error::last_os_error()));
            }
            let wrote = write_fd_impl(fd, &data[off..])?;
            off += wrote;
            pos += wrote as u64;
        }
        Ok(data.len())
    }
}

#[pyfunction]
#[pyo3(signature = (sock_fd, file_fd, max_bytes, cancel=None, file_offset=0))]
fn recv_to_fd(
    sock_fd: i64,
    file_fd: i32,
    max_bytes: u64,
    cancel: Option<PyRef<CancelFlag>>,
    file_offset: u64,
) -> PyResult<u64> {
    let no_cancel = AtomicBool::new(false);
    let flag = cancel
        .as_ref()
        .map(|c| &c.inner)
        .unwrap_or(&no_cancel);

    #[cfg(unix)]
    {
        recv_to_fd_impl(sock_fd as i32, file_fd, file_offset, max_bytes, flag).map_err(map_io_err)
    }
    #[cfg(windows)]
    {
        // SOCKET is pointer-sized on Win64 — must not truncate to i32.
        recv_to_fd_impl(sock_fd as RawSocket, file_fd, file_offset, max_bytes, flag)
            .map_err(map_io_err)
    }
}

#[pyfunction]
fn socket_send_all(sock_fd: i64, data: &[u8]) -> PyResult<()> {
    #[cfg(not(windows))]
    {
        let _ = (sock_fd, data);
        Err(PyOSError::new_err("socket_send_all is Windows-only"))
    }
    #[cfg(windows)]
    {
        socket_send_all_impl(sock_fd as RawSocket, data).map_err(map_io_err)
    }
}

#[pyfunction]
fn socket_close(sock_fd: i64) -> PyResult<()> {
    #[cfg(not(windows))]
    {
        let _ = sock_fd;
        Ok(())
    }
    #[cfg(windows)]
    {
        socket_close_impl(sock_fd as RawSocket);
        Ok(())
    }
}

#[pyfunction]
#[pyo3(signature = (sock_fd, file_path, start=0, length=None))]
fn transmit_file(
    sock_fd: i64,
    file_path: &str,
    start: u64,
    length: Option<u64>,
) -> PyResult<u64> {
    #[cfg(not(windows))]
    {
        let _ = (sock_fd, file_path, start, length);
        Err(PyOSError::new_err(
            "transmit_file is only available on Windows",
        ))
    }
    #[cfg(windows)]
    {
        let meta = std::fs::metadata(file_path).map_err(map_io_err)?;
        let file_size = meta.len();
        let end = length.map(|n| start.saturating_add(n)).unwrap_or(file_size);
        let count = end.min(file_size).saturating_sub(start);
        transmit_file_impl(sock_fd as RawSocket, file_path, start, count).map_err(map_io_err)
    }
}

#[pyfunction]
fn native_available() -> bool {
    true
}

#[pyfunction]
fn transmit_file_available() -> bool {
    cfg!(windows)
}

#[pymodule(gil_used = false)]
fn aird_transfer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(write_fd, m)?)?;
    m.add_function(wrap_pyfunction!(pwrite_fd, m)?)?;
    m.add_function(wrap_pyfunction!(recv_to_fd, m)?)?;
    m.add_function(wrap_pyfunction!(take_socket_from_iocp, m)?)?;
    m.add_function(wrap_pyfunction!(transmit_file, m)?)?;
    m.add_function(wrap_pyfunction!(socket_send_all, m)?)?;
    m.add_function(wrap_pyfunction!(socket_close, m)?)?;
    m.add_function(wrap_pyfunction!(native_available, m)?)?;
    m.add_function(wrap_pyfunction!(transmit_file_available, m)?)?;
    m.add_class::<CancelFlag>()?;
    http_upload::register(m)?;
    Ok(())
}
