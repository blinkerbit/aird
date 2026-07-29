//! Native fd pumps for AIRD HTTP uploads (used from Tornado handlers).
//!
//! Reads directly from a socket fd into a file fd without Python per-chunk copies.

use pyo3::exceptions::PyOSError;
use pyo3::prelude::*;
use std::sync::atomic::{AtomicBool, Ordering};

const BUF_SIZE: usize = 8 * 1024 * 1024;

#[cfg(unix)]
use std::os::unix::io::RawFd;

#[cfg(windows)]
use std::io::Read;
#[cfg(windows)]
use std::net::TcpStream;
#[cfg(windows)]
use std::mem::ManuallyDrop;
#[cfg(windows)]
use std::os::windows::io::{FromRawSocket, RawSocket};

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

fn map_io_err(err: std::io::Error) -> PyErr {
    PyOSError::new_err(err.to_string())
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
fn recv_to_fd_impl(
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
fn recv_to_fd_impl(
    sock_fd: RawSocket,
    file_fd: i32,
    _file_offset: u64,
    max_bytes: u64,
    cancel: &AtomicBool,
) -> Result<u64, std::io::Error> {
    let mut stream = ManuallyDrop::new(unsafe { TcpStream::from_raw_socket(sock_fd) });
    let mut buf = vec![0u8; BUF_SIZE];
    let mut total: u64 = 0;

    while total < max_bytes {
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        let want = (max_bytes - total).min(BUF_SIZE as u64) as usize;
        let n = stream.read(&mut buf[..want])?;
        if n == 0 {
            break;
        }
        write_fd_impl(file_fd, &buf[..n])?;
        total += n as u64;
    }
    Ok(total)
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
            let n = {
                unsafe {
                    libc::_lseek(fd, pos as libc::off_t, 0)
                }
            };
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
    sock_fd: i32,
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
        recv_to_fd_impl(sock_fd, file_fd, file_offset, max_bytes, flag).map_err(map_io_err)
    }
    #[cfg(windows)]
    {
        recv_to_fd_impl(sock_fd as RawSocket, file_fd, file_offset, max_bytes, flag)
            .map_err(map_io_err)
    }
}

#[pyfunction]
fn native_available() -> bool {
    true
}

#[pymodule(gil_used = false)]
fn aird_transfer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(write_fd, m)?)?;
    m.add_function(wrap_pyfunction!(pwrite_fd, m)?)?;
    m.add_function(wrap_pyfunction!(recv_to_fd, m)?)?;
    m.add_function(wrap_pyfunction!(native_available, m)?)?;
    m.add_class::<CancelFlag>()?;
    Ok(())
}
