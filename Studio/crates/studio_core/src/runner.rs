//! Execução de um `CmdSpec` com streaming de stdout/stderr e cancelamento.

use std::{
    io::{BufRead, BufReader, Read},
    process::{Child, Stdio},
    sync::{
        Arc,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};

use crossbeam_channel::Sender;

use crate::{
    cli::CmdSpec,
    events::{PipelineEvent, Stream, parse_progress},
};

#[derive(Debug)]
pub struct RunHandle {
    pid: Option<u32>,
    cancel: Arc<AtomicBool>,
    started: Instant,
}

impl RunHandle {
    pub fn pid(&self) -> Option<u32> {
        self.pid
    }

    pub fn is_cancelled(&self) -> bool {
        self.cancel.load(Ordering::SeqCst)
    }

    pub fn elapsed(&self) -> Duration {
        self.started.elapsed()
    }

    /// Pede terminação do grupo de processos (SIGTERM, depois SIGKILL).
    pub fn cancel(&self) {
        if self.cancel.swap(true, Ordering::SeqCst) {
            return;
        }
        if let Some(pid) = self.pid {
            terminate_group(pid);
        }
    }
}

pub fn spawn(spec: CmdSpec, tx: Sender<PipelineEvent>) -> std::io::Result<RunHandle> {
    let started = Instant::now();
    let mut command = spec.command();
    command.stdout(Stdio::piped()).stderr(Stdio::piped());
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        // PGID próprio: o cancelamento mata também os sub-processos das tools.
        command.process_group(0);
    }

    let mut child = command.spawn()?;
    let pid = child.id();
    let stdout = child.stdout.take();
    let stderr = child.stderr.take();
    let cancel = Arc::new(AtomicBool::new(false));

    let _ = tx.send(PipelineEvent::Started {
        command: spec.display(),
    });

    let stdout_thread = stdout.map(|reader| {
        let tx = tx.clone();
        thread::spawn(move || read_stream(reader, Stream::Stdout, tx))
    });
    let stderr_thread = stderr.map(|reader| {
        let tx = tx.clone();
        thread::spawn(move || read_stream(reader, Stream::Stderr, tx))
    });

    let cancel_for_wait = cancel.clone();
    let tx_for_wait = tx.clone();
    thread::spawn(move || {
        let status = wait_child(&mut child);
        if let Some(handle) = stdout_thread {
            let _ = handle.join();
        }
        if let Some(handle) = stderr_thread {
            let _ = handle.join();
        }
        let cancelled = cancel_for_wait.load(Ordering::SeqCst);
        let (code, success) = match status {
            Ok(status) => (status.code(), status.success()),
            Err(err) => {
                let _ = tx_for_wait.send(PipelineEvent::SpawnError(format!("wait: {err}")));
                (None, false)
            }
        };
        let _ = tx_for_wait.send(PipelineEvent::Finished {
            code,
            success: success && !cancelled,
            cancelled,
            duration: started.elapsed(),
        });
    });

    Ok(RunHandle {
        pid: Some(pid),
        cancel,
        started,
    })
}

fn wait_child(child: &mut Child) -> std::io::Result<std::process::ExitStatus> {
    child.wait()
}

fn read_stream<R: Read>(reader: R, stream: Stream, tx: Sender<PipelineEvent>) {
    let mut reader = BufReader::new(reader);
    let mut buf = Vec::new();
    loop {
        buf.clear();
        match reader.read_until(b'\n', &mut buf) {
            Ok(0) => break,
            Ok(_) => {
                let line = String::from_utf8_lossy(&buf);
                let line = line.trim_end_matches(['\r', '\n']).to_string();
                if line.trim().is_empty() {
                    continue;
                }
                if stream == Stream::Stdout
                    && let Some(event) = parse_progress(&line)
                {
                    if tx.send(PipelineEvent::Progress(event)).is_err() {
                        return;
                    }
                    continue;
                }
                if tx
                    .send(PipelineEvent::Line {
                        stream,
                        line: line.to_string(),
                    })
                    .is_err()
                {
                    return;
                }
            }
            Err(_) => break,
        }
    }
}

#[cfg(unix)]
fn terminate_group(pid: u32) {
    unsafe {
        libc::killpg(pid as libc::pid_t, libc::SIGTERM);
    }
    thread::spawn(move || {
        for _ in 0..30 {
            thread::sleep(Duration::from_millis(100));
            let alive = unsafe { libc::killpg(pid as libc::pid_t, 0) } == 0;
            if !alive {
                return;
            }
        }
        unsafe {
            libc::killpg(pid as libc::pid_t, libc::SIGKILL);
        }
    });
}

#[cfg(not(unix))]
fn terminate_group(pid: u32) {
    let _ = std::process::Command::new("taskkill")
        .args(["/T", "/F", "/PID", &pid.to_string()])
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();
}
