use nix::libc;
use nix::sys::wait::waitpid;
use nix::unistd::{ForkResult, Pid, fork, pipe};
use std::os::fd::{AsRawFd, OwnedFd};

use super::resolution::resolve_and_exec;
use super::types::{CommandSpec, ShellResult};

/// Execute a CommandSpec in a pipeline stage (doesn't return on success)
pub fn exec_pipeline_stage(spec: &CommandSpec) -> ! {
    match spec {
        CommandSpec::Command { program, args } => {
            resolve_and_exec(program, args);
        }
        CommandSpec::Builtin { .. } => {
            // Execute the builtin in a subshell and exit with its result
            let result = super::execute_command_spec(spec);
            std::process::exit(result.exit_code as i32);
        }
        CommandSpec::Subshell { runnable } => {
            // Execute the subshell and exit with its result
            let result = super::execute_command_spec(runnable);
            std::process::exit(result.exit_code as i32);
        }
        CommandSpec::Redirect { .. } => {
            // Execute the redirect and exit with its result
            let result = super::execute_command_spec(spec);
            std::process::exit(result.exit_code as i32);
        }
        CommandSpec::Pipeline { .. } => {
            panic!("Nested pipelines are impossible due to operator flattening");
        }
    }
}

/// Execute a pipeline: predecessors → last
pub fn run_pipeline(predecessors: &[CommandSpec], final_cmd: &CommandSpec) -> ShellResult {
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
        // Save original stdin
        let saved_stdin = unsafe { libc::dup(0) };
        if saved_stdin == -1 {
            panic!("Failed to save stdin");
        }

        // Redirect stdin from last pipe (if any)
        if num_pipes > 0 {
            unsafe {
                libc::dup2(pipes[num_pipes - 1].0.as_raw_fd(), 0);
            }
        }

        // Close all pipe file descriptors
        drop(pipes);

        // Wait for all predecessor children before executing
        for child_pid in child_pids {
            waitpid(child_pid, None).ok();
        }

        // Execute builtin directly in parent (no fork)
        let exit_code = func(args);
        let result = ShellResult {
            exit_code: exit_code as u8,
        };

        // Restore original stdin
        unsafe {
            libc::dup2(saved_stdin, 0);
            libc::close(saved_stdin);
        }

        result
    } else {
        // Fork and execute the last command (regular commands)
        let last_child = match unsafe { fork() } {
            Ok(ForkResult::Parent { child }) => child,
            Ok(ForkResult::Child) => {
                // Redirect stdin from last pipe
                if num_pipes > 0 {
                    unsafe {
                        libc::dup2(pipes[num_pipes - 1].0.as_raw_fd(), 0);
                    }
                }
                // stdout inherits from parent (goes to terminal)

                // Close all pipe file descriptors
                drop(pipes);

                // Execute the final command or subshell
                exec_pipeline_stage(final_cmd);
            }
            Err(e) => {
                panic!("fork failed: {}", e);
            }
        };

        // Parent: close all pipe file descriptors (automatically dropped)
        drop(pipes);

        // Wait for all predecessor children
        for child_pid in child_pids {
            waitpid(child_pid, None).ok();
        }

        // Wait for the last child and return its exit code
        super::wait_for_child(last_child)
    }
}
