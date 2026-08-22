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
fn recv_to_fd_impl(
    sock_fd: RawFd,
    file_fd: RawFd,
    max_bytes: u64,
    cancel: &AtomicBool,
) -> Result<u64, std::io::Error> {
    let mut buf = vec![0u8; BUF_SIZE];
    let mut total: u64 = 0;

    while total < max_bytes {
        if cancel.load(Ordering::Relaxed) {
            break;
        }
        let want = (max_bytes - total).min(BUF_SIZE as u64) as usize;
        let n = unsafe {
            libc::read(
                sock_fd,
                buf.as_mut_ptr() as *mut libc::c_void,
                want,
            )
        };
        if n < 0 {
            return Err(std::io::Error::last_os_error());
        }
        if n == 0 {
            break;
        }
        let mut written = 0;
        while written < n as usize {
            let w = unsafe {
                libc::write(
                    file_fd,
                    buf[written..n as usize].as_ptr() as *const libc::c_void,
                    (n as usize) - written,
                )
            };
            if w < 0 {
                return Err(std::io::Error::last_os_error());
            }
            if w == 0 {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::WriteZero,
                    "write returned 0",
                ));
            }
            written += w as usize;
        }
        total += n as u64;
    }
    Ok(total)
}

#[cfg(windows)]
fn recv_to_fd_impl(
    sock_fd: RawSocket,
    file_fd: i32,
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
#[pyo3(signature = (sock_fd, file_fd, max_bytes, cancel=None))]
fn recv_to_fd(
    sock_fd: i32,
    file_fd: i32,
    max_bytes: u64,
    cancel: Option<PyRef<CancelFlag>>,
) -> PyResult<u64> {
    let no_cancel = AtomicBool::new(false);
    let flag = cancel
        .as_ref()
        .map(|c| &c.inner)
        .unwrap_or(&no_cancel);

    #[cfg(unix)]
    {
        recv_to_fd_impl(sock_fd, file_fd, max_bytes, flag).map_err(map_io_err)
    }
    #[cfg(windows)]
    {
        recv_to_fd_impl(sock_fd as RawSocket, file_fd, max_bytes, flag).map_err(map_io_err)
    }
}

#[pyfunction]
fn native_available() -> bool {
    true
}

#[pymodule]
fn aird_transfer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(write_fd, m)?)?;
    m.add_function(wrap_pyfunction!(recv_to_fd, m)?)?;
    m.add_function(wrap_pyfunction!(native_available, m)?)?;
    m.add_class::<CancelFlag>()?;
    Ok(())
}
