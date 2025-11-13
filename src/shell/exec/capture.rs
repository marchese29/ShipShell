use nix::libc;
use nix::sys::wait::{WaitStatus, waitpid};
use nix::unistd::{ForkResult, Pid, fork, pipe};
use std::collections::HashMap;
use std::os::unix::io::{AsRawFd, IntoRawFd};

use super::resolution::resolve_and_exec;
use super::types::{CommandSpec, ShellResult};
use crate::shell::env::{EnvValue, get_shell_env};

/// Wait for a child and return captured result with FDs
fn wait_for_child_captured(child: Pid, stdout_fd: i32, stderr_fd: i32) -> ShellResult {
    match waitpid(child, None) {
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
}

/// Internal execution with capture: Execute a CommandSpec and capture stdout/stderr
pub(super) fn execute_command_spec_with_capture(spec: &CommandSpec) -> ShellResult {
    match spec {
        CommandSpec::Command { program, args } => execute_command_captured(program, args),
        CommandSpec::Builtin { func, args, .. } => execute_builtin_captured(func, args),
        CommandSpec::Pipeline {
            predecessors,
            final_cmd,
        } => {
            // For pipelines, we only capture the final command's output
            // Predecessors write to pipes as normal
            super::pipeline::run_pipeline_captured(predecessors, final_cmd)
        }
        CommandSpec::Subshell { runnable } => execute_subshell_captured(runnable),
        CommandSpec::Redirect { runnable, target } => {
            // Redirect wins - execute normally and return empty capture
            // The output goes to the file, not our pipes
            let result = super::execute_redirect(runnable, target);

            // Create dummy pipes that are already closed (empty)
            let (stdout_read, stdout_write) = pipe().expect("Failed to create pipe");
            let (stderr_read, stderr_write) = pipe().expect("Failed to create pipe");

            // Close write ends immediately (no data will be written)
            drop(stdout_write);
            drop(stderr_write);

            // Leak the read ends and return
            ShellResult::Captured {
                exit_code: result.exit_code(),
                stdout_fd: stdout_read.into_raw_fd(),
                stderr_fd: stderr_read.into_raw_fd(),
            }
        }
        CommandSpec::WithEnv {
            runnable,
            env_overlay,
        } => execute_with_env_captured(runnable, env_overlay),
    }
}

/// Execute a command with stdout/stderr capture
fn execute_command_captured(program: &str, args: &[String]) -> ShellResult {
    // Create pipes for stdout and stderr
    let (stdout_read, stdout_write) = pipe().expect("Failed to create stdout pipe");
    let (stderr_read, stderr_write) = pipe().expect("Failed to create stderr pipe");

    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => {
            // Parent: close write ends
            drop(stdout_write);
            drop(stderr_write);

            // Leak read ends and wait for child
            let stdout_fd = stdout_read.into_raw_fd();
            let stderr_fd = stderr_read.into_raw_fd();
            wait_for_child_captured(child, stdout_fd, stderr_fd)
        }
        Ok(ForkResult::Child) => {
            // Child: close read ends and redirect stdout/stderr
            drop(stdout_read);
            drop(stderr_read);

            unsafe {
                libc::dup2(stdout_write.as_raw_fd(), 1); // stdout
                libc::dup2(stderr_write.as_raw_fd(), 2); // stderr
            }
            drop(stdout_write);
            drop(stderr_write);

            // Execute the program
            resolve_and_exec(program, args);
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute a builtin with stdout/stderr capture
fn execute_builtin_captured(func: &fn(&[String]) -> i32, args: &[String]) -> ShellResult {
    // Create pipes for stdout and stderr
    let (stdout_read, stdout_write) = pipe().expect("Failed to create stdout pipe");
    let (stderr_read, stderr_write) = pipe().expect("Failed to create stderr pipe");

    // Save original stdout and stderr
    let saved_stdout = unsafe { libc::dup(1) };
    let saved_stderr = unsafe { libc::dup(2) };
    if saved_stdout == -1 || saved_stderr == -1 {
        panic!("Failed to save stdout/stderr");
    }

    // Redirect stdout and stderr to pipes
    unsafe {
        libc::dup2(stdout_write.as_raw_fd(), 1);
        libc::dup2(stderr_write.as_raw_fd(), 2);
    }

    // Close write ends (dup2 created copies at fd 1 and 2)
    drop(stdout_write);
    drop(stderr_write);

    // Execute the builtin
    let exit_code = func(args);

    // Restore original stdout and stderr
    unsafe {
        libc::dup2(saved_stdout, 1);
        libc::dup2(saved_stderr, 2);
        libc::close(saved_stdout);
        libc::close(saved_stderr);
    }

    // Leak read ends and return
    ShellResult::Captured {
        exit_code: exit_code as u8,
        stdout_fd: stdout_read.into_raw_fd(),
        stderr_fd: stderr_read.into_raw_fd(),
    }
}

/// Execute a subshell with capture
fn execute_subshell_captured(spec: &CommandSpec) -> ShellResult {
    // Create pipes for stdout and stderr
    let (stdout_read, stdout_write) = pipe().expect("Failed to create stdout pipe");
    let (stderr_read, stderr_write) = pipe().expect("Failed to create stderr pipe");

    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => {
            // Parent: close write ends
            drop(stdout_write);
            drop(stderr_write);

            // Leak read ends and wait for child
            let stdout_fd = stdout_read.into_raw_fd();
            let stderr_fd = stderr_read.into_raw_fd();
            wait_for_child_captured(child, stdout_fd, stderr_fd)
        }
        Ok(ForkResult::Child) => {
            // Child: close read ends and redirect stdout/stderr
            drop(stdout_read);
            drop(stderr_read);

            unsafe {
                libc::dup2(stdout_write.as_raw_fd(), 1);
                libc::dup2(stderr_write.as_raw_fd(), 2);
            }
            drop(stdout_write);
            drop(stderr_write);

            // Execute the subshell command (without additional capture)
            let result = super::execute_command_spec(spec);
            std::process::exit(result.exit_code() as i32);
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute command with environment overlay and capture
fn execute_with_env_captured(
    spec: &CommandSpec,
    overlay: &HashMap<String, EnvValue>,
) -> ShellResult {
    // Save current environment state for variables in the overlay
    let env = get_shell_env();
    let saved_vars: HashMap<String, Option<EnvValue>> = {
        let env_read = env.read().unwrap();
        overlay
            .keys()
            .map(|k| (k.clone(), env_read.get(k).cloned()))
            .collect()
    };

    // Apply overlay to environment
    {
        let mut env_write = env.write().unwrap();
        for (key, value) in overlay {
            env_write.set(key.clone(), value.clone());
        }
    }

    // Execute wrapped command with capture
    let result = execute_command_spec_with_capture(spec);

    // Restore original environment
    {
        let mut env_write = env.write().unwrap();
        for (key, original_value) in saved_vars {
            match original_value {
                Some(value) => env_write.set(key, value),
                None => {
                    env_write.unset(&key);
                }
            }
        }
    }

    result
}
