mod pipeline;
mod resolution;
mod types;

use nix::libc;
use nix::sys::wait::{WaitStatus, waitpid};
use nix::unistd::{ForkResult, Pid, fork};

// Re-export public types
pub use types::{ExecRequest, RedirectTarget, ShellResult};

use pipeline::run_pipeline;
use resolution::resolve_and_exec;
use types::CommandSpec;

/// Public interface: Execute an ExecRequest (command, pipeline, subshell, or redirect)
pub fn execute(request: &ExecRequest) -> ShellResult {
    let spec = CommandSpec::from(request);
    execute_command_spec(&spec)
}

/// Internal execution: Execute a CommandSpec
pub(crate) fn execute_command_spec(spec: &CommandSpec) -> ShellResult {
    match spec {
        CommandSpec::Command { program, args } => execute_command(program, args),
        CommandSpec::Builtin { func, args, .. } => {
            // Execute builtin directly in parent process
            let exit_code = func(args);
            ShellResult {
                exit_code: exit_code as u8,
            }
        }
        CommandSpec::Pipeline {
            predecessors,
            final_cmd,
        } => run_pipeline(predecessors, final_cmd),
        CommandSpec::Subshell { runnable } => execute_subshell(runnable),
        CommandSpec::Redirect { runnable, target } => execute_redirect(runnable, target),
    }
}

/// Helper to fork and run a child function, waiting for the result
/// The child function should return an exit code, which will be used to exit the child process
fn fork_and_run<F>(child_fn: F) -> ShellResult
where
    F: FnOnce() -> i32,
{
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => {
            let exit_code = child_fn();
            std::process::exit(exit_code);
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute a single command
fn execute_command(program: &str, args: &[String]) -> ShellResult {
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => resolve_and_exec(program, args),
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute command in a subshell
fn execute_subshell(spec: &CommandSpec) -> ShellResult {
    fork_and_run(|| {
        let result = execute_command_spec(spec); // Recursive!
        result.exit_code as i32
    })
}

/// Execute command with output redirection
fn execute_redirect(spec: &CommandSpec, target: &types::RedirectTarget) -> ShellResult {
    fork_and_run(|| {
        // Set up the output redirection
        match target {
            types::RedirectTarget::FilePath { path, append } => {
                // Open the file with appropriate flags
                use std::fs::OpenOptions;
                let file = OpenOptions::new()
                    .write(true)
                    .create(true)
                    .truncate(!append)
                    .append(*append)
                    .open(path);

                match file {
                    Ok(f) => {
                        use std::os::unix::io::IntoRawFd;
                        let fd = f.into_raw_fd();
                        // Redirect stdout to the file
                        unsafe {
                            libc::dup2(fd, 1);
                            libc::close(fd);
                        }
                    }
                    Err(e) => {
                        eprintln!("{}: {}", path, e);
                        return 1;
                    }
                }
            }
            types::RedirectTarget::FileDescriptor { fd } => {
                // Redirect stdout to the provided file descriptor
                unsafe {
                    libc::dup2(*fd, 1);
                    // Close the original fd since dup2 created a copy at fd 1
                    libc::close(*fd);
                }
            }
        }

        // Execute the inner command
        let result = execute_command_spec(spec);
        result.exit_code as i32
    })
}

/// Wait for a child and convert its status to ShellResult
pub(crate) fn wait_for_child(child: Pid) -> ShellResult {
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
