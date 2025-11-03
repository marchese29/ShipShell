use nix::libc;
use nix::sys::wait::{WaitStatus, waitpid};
use nix::unistd::{ForkResult, Pid, execvp, fork, pipe};
use std::ffi::CString;
use std::os::fd::{AsRawFd, OwnedFd};

#[derive(Debug, Clone)]
pub struct ShellResult {
    pub exit_code: u8,
}

#[derive(Debug, Clone)]
pub enum CommandSpec {
    Command {
        program: String,
        args: Vec<String>,
    },
    Pipeline {
        predecessors: Vec<CommandSpec>,
        final_cmd: Box<CommandSpec>,
    },
    Subshell {
        runnable: Box<CommandSpec>,
    },
}

/// Execute a command, pipeline, or subshell
pub fn execute(spec: &CommandSpec) -> ShellResult {
    match spec {
        CommandSpec::Command { program, args } => execute_command(program, args),
        CommandSpec::Pipeline {
            predecessors,
            final_cmd,
        } => execute_pipeline(predecessors, final_cmd),
        CommandSpec::Subshell { runnable } => execute_subshell(runnable),
    }
}

/// Execute a single command
fn execute_command(program: &str, args: &[String]) -> ShellResult {
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => {
            let prog_cstr = CString::new(program).expect("Program name contains null byte");
            let mut argv: Vec<CString> = Vec::new();
            argv.push(prog_cstr.clone());
            for arg in args {
                argv.push(CString::new(arg.as_str()).expect("Argument contains null byte"));
            }

            let err = execvp(&prog_cstr, &argv);
            eprintln!("Failed to execute {}: {}", program, err.unwrap_err());
            std::process::exit(127);
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute a pipeline
fn execute_pipeline(predecessors: &[CommandSpec], final_cmd: &CommandSpec) -> ShellResult {
    // For now, only support Command types in pipelines
    let pred_specs: Vec<(&str, &[String])> = predecessors
        .iter()
        .map(|spec| {
            if let CommandSpec::Command { program, args } = spec {
                (program.as_str(), args.as_slice())
            } else {
                panic!("Nested pipelines/subshells in pipeline not yet supported");
            }
        })
        .collect();

    let (final_prog, final_args) = if let CommandSpec::Command { program, args } = final_cmd {
        (program.as_str(), args.as_slice())
    } else {
        panic!("Pipeline final command must be a simple command");
    };

    run_pipeline(&pred_specs, final_prog, final_args)
}

/// Execute command in a subshell
fn execute_subshell(spec: &CommandSpec) -> ShellResult {
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => {
            let result = execute(spec); // Recursive!
            std::process::exit(result.exit_code as i32);
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Wait for a child and convert its status to ShellResult
fn wait_for_child(child: Pid) -> ShellResult {
    match waitpid(child, None) {
        Ok(WaitStatus::Exited(_pid, exit_code)) => ShellResult {
            exit_code: exit_code as u8,
        },
        Ok(WaitStatus::Signaled(_pid, signal, _core_dump)) => ShellResult {
            exit_code: 128 + (signal as i32) as u8,
        },
        Ok(status) => {
            panic!("Unexpected wait status: {:?}", status);
        }
        Err(e) => {
            panic!("waitpid failed: {}", e);
        }
    }
}

/// Execute a command in the current process (for use in pipeline children)
/// This function never returns on success
fn exec_in_pipeline(prog: &str, args: &[String]) -> ! {
    let prog_cstr = CString::new(prog).expect("Program name contains null byte");
    let mut argv: Vec<CString> = Vec::new();
    argv.push(prog_cstr.clone());
    for arg in args {
        argv.push(CString::new(arg.as_str()).expect("Argument contains null byte"));
    }

    let err = execvp(&prog_cstr, &argv);
    eprintln!("Failed to execute {}: {}", prog, err.unwrap_err());
    std::process::exit(127);
}

/// Execute a pipeline: predecessors → last
fn run_pipeline(
    predecessors: &[(&str, &[String])],
    final_prog: &str,
    final_args: &[String],
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

                // Execute the command
                let (prog, args) = spec;
                exec_in_pipeline(prog, args);
            }
            Err(e) => {
                panic!("fork failed: {}", e);
            }
        }
    }

    // Fork and execute the last command
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

            // Execute the command
            exec_in_pipeline(final_prog, final_args);
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
    wait_for_child(last_child)
}
