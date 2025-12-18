mod pipeline;
mod resolution;
mod types;

use nix::libc;
use nix::sys::wait::{WaitStatus, waitpid};
use nix::unistd::{ForkResult, Pid, fork, pipe};
use std::collections::HashMap;
use std::os::unix::io::IntoRawFd;

// Re-export public types
pub use types::{ExecRequest, ExecutionContext, RedirectTarget, ShellResult};

use crate::shell::env::{EnvValue, get_shell_env};
use pipeline::run_pipeline;
use resolution::{exec_resolved, resolve_program_path};
use types::{CommandSpec, RedirectOperation};

/// Public interface: Execute an ExecRequest (command, pipeline, subshell, or redirect)
pub fn execute(request: &ExecRequest) -> ShellResult {
    // BUILD EXECUTION CONTEXT ONCE (only place we read global env)
    let context = {
        let env = get_shell_env();
        let env_read = env.read().unwrap();

        let mut env_vars = HashMap::new();
        for (key, value) in env_read.all_vars() {
            if env_read.is_exported(key) {
                env_vars.insert(key.clone(), value.clone());
            }
        }

        ExecutionContext::new(env_vars)
    };

    let spec = CommandSpec::from(request);
    let result = execute_command_spec(&spec, context);

    // Update $? with the exit code
    crate::shell::set_last_exit(result.exit_code());

    result
}

/// Public interface: Execute an ExecRequest and capture stdout/stderr
/// Returns file descriptors that the caller must close
pub fn execute_with_capture(request: &ExecRequest) -> ShellResult {
    let spec = CommandSpec::from(request);
    let result = execute_command_spec_with_capture(&spec);

    // Update $? with the exit code
    crate::shell::set_last_exit(result.exit_code());

    result
}

/// Internal execution with capture: Execute a CommandSpec and capture stdout/stderr
fn execute_command_spec_with_capture(spec: &CommandSpec) -> ShellResult {
    // Build context with capture enabled
    let (stdout_read, stdout_write) = pipe().expect("Failed to create stdout pipe");
    let (stderr_read, stderr_write) = pipe().expect("Failed to create stderr pipe");

    // Get raw FDs for the write ends before moving them into context
    let stdout_write_fd = stdout_write.into_raw_fd();
    let stderr_write_fd = stderr_write.into_raw_fd();

    let context = {
        let env = get_shell_env();
        let env_read = env.read().unwrap();

        let mut env_vars = HashMap::new();
        for (key, value) in env_read.all_vars() {
            if env_read.is_exported(key) {
                env_vars.insert(key.clone(), value.clone());
            }
        }

        ExecutionContext::new(env_vars).with_capture(stdout_write_fd, stderr_write_fd)
    };

    // Execute with capture context (child will use write ends)
    let result = execute_command_spec(spec, context);

    // CRITICAL: Close write ends in parent so read ends can get EOF
    unsafe {
        libc::close(stdout_write_fd);
        libc::close(stderr_write_fd);
    }

    // Return captured result with read ends
    ShellResult::Captured {
        exit_code: result.exit_code(),
        stdout_fd: stdout_read.into_raw_fd(),
        stderr_fd: stderr_read.into_raw_fd(),
    }
}

/// Internal execution: Execute a CommandSpec with execution context
pub(crate) fn execute_command_spec(spec: &CommandSpec, context: ExecutionContext) -> ShellResult {
    match spec {
        CommandSpec::Command { program, args } => execute_command(program, args, context),

        CommandSpec::Builtin { func, args, .. } => {
            // Builtins run in parent process, modifying global env directly
            let exit_code = func(args);
            ShellResult::ExitOnly {
                exit_code: exit_code as u8,
            }
        }

        CommandSpec::Pipeline {
            predecessors,
            final_cmd,
        } => run_pipeline(predecessors, final_cmd, context),

        CommandSpec::Subshell { runnable } => execute_subshell(runnable, context),

        CommandSpec::Redirect { runnable, target } => execute_redirect(runnable, target, context),

        CommandSpec::WithEnv {
            runnable,
            env_overlay,
        } => execute_with_env(runnable, env_overlay, context),

        CommandSpec::ScriptExec { code } => execute_script(code),

        CommandSpec::Negated { runnable } => execute_negated(runnable, context),
    }
}

/// Execute a single command - LEAF operation (only one that forks for commands)
fn execute_command(program: &str, args: &[String], context: ExecutionContext) -> ShellResult {
    // Resolve path BEFORE fork (reads from context.env_vars, no locks)
    let resolved_path = match resolve_program_path(program, &context.env_vars) {
        Ok(path) => path,
        Err(error) => {
            eprintln!("{}", error.message());
            return ShellResult::ExitOnly {
                exit_code: error.exit_code() as u8,
            };
        }
    };

    // Fork once
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => {
            // Execute in child (applies context: redirects, capture, then execve)
            exec_resolved(program, &resolved_path, args, context)
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute command in a subshell
fn execute_subshell(spec: &CommandSpec, context: ExecutionContext) -> ShellResult {
    // TODO: In the future, this should re-invoke ship via execve to properly
    // isolate the environment (including Python locals/globals). For now,
    // we fork and recurse which works in single-threaded mode but may have
    // issues with more complex state.
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => {
            let result = execute_command_spec(spec, context);
            std::process::exit(result.exit_code() as i32);
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute command with output redirection - adds redirect to context, recurses (NO FORK)
fn execute_redirect(
    spec: &CommandSpec,
    target: &types::RedirectTarget,
    context: ExecutionContext,
) -> ShellResult {
    // Convert RedirectTarget to RedirectOperation
    let redirect_op = match target {
        types::RedirectTarget::FilePath { path, append } => RedirectOperation::FileOutput {
            path: path.clone(),
            append: *append,
            fd: 1, // stdout
        },
        types::RedirectTarget::FileDescriptor { fd } => RedirectOperation::FileDescriptor {
            from_fd: *fd,
            to_fd: 1, // redirect to stdout
        },
    };

    // Add redirect to context and recurse (no fork here - let child decide)
    let new_context = context.with_redirect(redirect_op);
    execute_command_spec(spec, new_context)
}

/// Execute Script code using the global CODE_EXECUTOR
fn execute_script(code: &str) -> ShellResult {
    if let Some(executor) = crate::shell::get_code_executor() {
        match executor(code) {
            Ok(()) => ShellResult::ExitOnly { exit_code: 0 },
            Err(_) => ShellResult::ExitOnly { exit_code: 1 },
        }
    } else {
        eprintln!("Error: Code executor not initialized");
        ShellResult::ExitOnly { exit_code: 1 }
    }
}

/// Execute command with environment overlay - merges env into context, recurses (NO FORK)
fn execute_with_env(
    spec: &CommandSpec,
    overlay: &HashMap<String, EnvValue>,
    context: ExecutionContext,
) -> ShellResult {
    // Merge overlay into context and recurse (no fork - let child decide)
    let new_context = context.with_env_overlay(overlay);
    execute_command_spec(spec, new_context)
}

/// Execute a negated command - recurses, inverts exit code (NO FORK)
fn execute_negated(spec: &CommandSpec, context: ExecutionContext) -> ShellResult {
    let result = execute_command_spec(spec, context);
    let inverted_code = if result.exit_code() == 0 { 1 } else { 0 };

    ShellResult::ExitOnly {
        exit_code: inverted_code,
    }
}

/// Wait for a child and convert its status to ShellResult
pub(crate) fn wait_for_child(child: Pid) -> ShellResult {
    match waitpid(child, None) {
        Ok(WaitStatus::Exited(_pid, exit_code)) => ShellResult::ExitOnly {
            exit_code: exit_code as u8,
        },
        Ok(WaitStatus::Signaled(_pid, signal, _core_dump)) => ShellResult::ExitOnly {
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
