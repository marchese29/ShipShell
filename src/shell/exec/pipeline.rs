use nix::libc;
use nix::sys::wait::{WaitStatus, waitpid};
use nix::unistd::{ForkResult, Pid, fork, pipe};
use std::os::fd::{AsRawFd, IntoRawFd, OwnedFd};

use super::resolution::resolve_and_exec;
use super::types::{CommandSpec, ShellResult};

/// Execute a CommandSpec in a pipeline stage (doesn't return on success)
pub fn exec_pipeline_stage(spec: &CommandSpec) -> ! {
    match spec {
        CommandSpec::Command { program, args } => {
            resolve_and_exec(program, args);
        }
        CommandSpec::Builtin { .. }
        | CommandSpec::Redirect { .. }
        | CommandSpec::WithEnv { .. } => {
            // Execute the builtin in a subshell and exit with its result
            let result = super::execute_command_spec(spec);
            std::process::exit(result.exit_code() as i32);
        }
        CommandSpec::Subshell { runnable } => {
            // Execute the subshell and exit with its result
            let result = super::execute_command_spec(runnable);
            std::process::exit(result.exit_code() as i32);
        }
        CommandSpec::Pipeline { .. } => {
            panic!("Nested pipelines are impossible due to operator flattening");
        }
    }
}

/// Helper to execute a pipeline with optional output capture
/// If capture_pipes is Some, the final command's stdout/stderr are captured
/// If capture_pipes is None, the final command inherits stdout/stderr
fn run_pipeline_internal(
    predecessors: &[CommandSpec],
    final_cmd: &CommandSpec,
    capture_pipes: Option<(OwnedFd, OwnedFd, OwnedFd, OwnedFd)>, // (stdout_read, stdout_write, stderr_read, stderr_write)
) -> ShellResult {
    let num_pipes = predecessors.len();

    // Create all pipes
    let mut pipes: Vec<(OwnedFd, OwnedFd)> = Vec::new();
    for _ in 0..num_pipes {
        let (read_fd, write_fd) = pipe().expect("Failed to create pipe");
        pipes.push((read_fd, write_fd));
    }

    // Track all child PIDs
    let mut child_pids: Vec<Pid> = Vec::new();

    // Fork and execute each predecessor
    for (i, spec) in predecessors.iter().enumerate() {
        match unsafe { fork() } {
            Ok(ForkResult::Parent { child }) => {
                child_pids.push(child);
            }
            Ok(ForkResult::Child) => {
                // Redirect stdin from previous pipe (if not first)
                if i > 0 {
                    unsafe {
                        libc::dup2(pipes[i - 1].0.as_raw_fd(), 0);
                    }
                }

                // Redirect stdout to current pipe
                unsafe {
                    libc::dup2(pipes[i].1.as_raw_fd(), 1);
                }

                // Close all pipe file descriptors (they get closed when dropped anyway)
                drop(pipes);

                // Execute the command or subshell
                exec_pipeline_stage(spec);
            }
            Err(e) => {
                panic!("fork failed: {}", e);
            }
        }
    }

    // Check if final command is a builtin - if so, execute in parent for efficiency
    if let CommandSpec::Builtin { func, args, .. } = final_cmd {
        // Save original stdin, and possibly stdout/stderr if capturing
        let saved_stdin = unsafe { libc::dup(0) };
        let (saved_stdout, saved_stderr) = if capture_pipes.is_some() {
            let out = unsafe { libc::dup(1) };
            let err = unsafe { libc::dup(2) };
            (Some(out), Some(err))
        } else {
            (None, None)
        };

        if saved_stdin == -1
            || (saved_stdout.is_some() && saved_stdout.unwrap() == -1)
            || (saved_stderr.is_some() && saved_stderr.unwrap() == -1)
        {
            panic!("Failed to save stdin/stdout/stderr");
        }

        // Redirect stdin from last pipe (if any)
        if num_pipes > 0 {
            unsafe {
                libc::dup2(pipes[num_pipes - 1].0.as_raw_fd(), 0);
            }
        }

        // If capturing, redirect stdout/stderr to capture pipes
        let capture_fds =
            if let Some((stdout_read, stdout_write, stderr_read, stderr_write)) = capture_pipes {
                unsafe {
                    libc::dup2(stdout_write.as_raw_fd(), 1);
                    libc::dup2(stderr_write.as_raw_fd(), 2);
                }
                drop(stdout_write);
                drop(stderr_write);
                Some((stdout_read, stderr_read))
            } else {
                None
            };

        // Close all pipe file descriptors
        drop(pipes);

        // Wait for all predecessor children before executing
        for child_pid in child_pids {
            waitpid(child_pid, None).ok();
        }

        // Execute builtin directly in parent (no fork)
        let exit_code = func(args);

        // Restore original stdin and possibly stdout/stderr
        unsafe {
            libc::dup2(saved_stdin, 0);
            libc::close(saved_stdin);
            if let Some(out) = saved_stdout {
                libc::dup2(out, 1);
                libc::close(out);
            }
            if let Some(err) = saved_stderr {
                libc::dup2(err, 2);
                libc::close(err);
            }
        }

        // Return appropriate result variant
        if let Some((stdout_read, stderr_read)) = capture_fds {
            ShellResult::Captured {
                exit_code: exit_code as u8,
                stdout_fd: stdout_read.into_raw_fd(),
                stderr_fd: stderr_read.into_raw_fd(),
            }
        } else {
            ShellResult::ExitOnly {
                exit_code: exit_code as u8,
            }
        }
    } else {
        // Fork and execute the last command (regular commands)
        let capture_fds =
            if let Some((stdout_read, stdout_write, stderr_read, stderr_write)) = capture_pipes {
                // We're capturing - set up in child
                Some((stdout_read, stdout_write, stderr_read, stderr_write))
            } else {
                None
            };

        let last_child = match unsafe { fork() } {
            Ok(ForkResult::Parent { child }) => child,
            Ok(ForkResult::Child) => {
                // Redirect stdin from last pipe
                if num_pipes > 0 {
                    unsafe {
                        libc::dup2(pipes[num_pipes - 1].0.as_raw_fd(), 0);
                    }
                }

                // If capturing, redirect stdout/stderr to capture pipes
                if let Some((_, ref stdout_write, _, ref stderr_write)) = capture_fds {
                    unsafe {
                        libc::dup2(stdout_write.as_raw_fd(), 1);
                        libc::dup2(stderr_write.as_raw_fd(), 2);
                    }
                }

                // Close all pipe file descriptors
                drop(pipes);
                drop(capture_fds);

                // Execute the final command or subshell
                exec_pipeline_stage(final_cmd);
            }
            Err(e) => {
                panic!("fork failed: {}", e);
            }
        };

        // Parent: close all pipe file descriptors and write ends of capture pipes
        drop(pipes);
        let leaked_fds =
            if let Some((stdout_read, stdout_write, stderr_read, stderr_write)) = capture_fds {
                drop(stdout_write);
                drop(stderr_write);
                Some((stdout_read, stderr_read))
            } else {
                None
            };

        // Wait for all predecessor children
        for child_pid in child_pids {
            waitpid(child_pid, None).ok();
        }

        // Wait for the last child and return result
        if let Some((stdout_read, stderr_read)) = leaked_fds {
            // Capturing - wait and return Captured variant
            let stdout_fd = stdout_read.into_raw_fd();
            let stderr_fd = stderr_read.into_raw_fd();

            match waitpid(last_child, None) {
                Ok(WaitStatus::Exited(_pid, exit_code)) => ShellResult::Captured {
                    exit_code: exit_code as u8,
                    stdout_fd,
                    stderr_fd,
                },
                Ok(WaitStatus::Signaled(_pid, signal, _core_dump)) => ShellResult::Captured {
                    exit_code: 128 + (signal as i32) as u8,
                    stdout_fd,
                    stderr_fd,
                },
                Ok(status) => {
                    panic!("Unexpected wait status: {:?}", status);
                }
                Err(e) => {
                    panic!("waitpid failed: {}", e);
                }
            }
        } else {
            // Not capturing - use normal wait_for_child
            super::wait_for_child(last_child)
        }
    }
}

/// Execute a pipeline: predecessors → last (normal execution, no capture)
pub fn run_pipeline(predecessors: &[CommandSpec], final_cmd: &CommandSpec) -> ShellResult {
    run_pipeline_internal(predecessors, final_cmd, None)
}

/// Execute a pipeline with output capture on the final command
pub(super) fn run_pipeline_captured(
    predecessors: &[CommandSpec],
    final_cmd: &CommandSpec,
) -> ShellResult {
    // Create capture pipes
    let (stdout_read, stdout_write) = pipe().expect("Failed to create stdout pipe");
    let (stderr_read, stderr_write) = pipe().expect("Failed to create stderr pipe");

    run_pipeline_internal(
        predecessors,
        final_cmd,
        Some((stdout_read, stdout_write, stderr_read, stderr_write)),
    )
}
