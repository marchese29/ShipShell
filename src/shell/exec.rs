use nix::libc;
use nix::sys::wait::{WaitStatus, waitpid};
use nix::unistd::{ForkResult, Pid, execve, fork, pipe};
use std::ffi::CString;
use std::os::fd::{AsRawFd, OwnedFd};
use std::path::PathBuf;

use super::env::{EnvValue, get_shell_env, get_var};

#[derive(Debug, Clone)]
pub struct ShellResult {
    pub exit_code: u8,
}

/// Represents errors that can occur during program path resolution
#[derive(Debug)]
enum ProgramResolutionError {
    /// Command not found in PATH
    NotFound(String),
    /// File doesn't exist (for paths with '/')
    NoSuchFile(String),
    /// File exists but is not executable
    PermissionDenied(String),
    /// PATH environment variable has invalid configuration
    InvalidPath(String),
}

impl ProgramResolutionError {
    /// Get the appropriate exit code for this error type
    fn exit_code(&self) -> i32 {
        match self {
            ProgramResolutionError::NotFound(_) => 127,
            ProgramResolutionError::NoSuchFile(_) => 127,
            ProgramResolutionError::PermissionDenied(_) => 126,
            ProgramResolutionError::InvalidPath(_) => 127,
        }
    }

    /// Get the error message
    fn message(&self) -> &str {
        match self {
            ProgramResolutionError::NotFound(msg) => msg,
            ProgramResolutionError::NoSuchFile(msg) => msg,
            ProgramResolutionError::PermissionDenied(msg) => msg,
            ProgramResolutionError::InvalidPath(msg) => msg,
        }
    }
}

#[derive(Debug, Clone)]
pub enum RedirectTarget {
    FilePath { path: String, append: bool },
    FileDescriptor { fd: i32 },
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
    Redirect {
        runnable: Box<CommandSpec>,
        target: RedirectTarget,
    },
}

/// Execute a command, pipeline, subshell, or redirect
pub fn execute(spec: &CommandSpec) -> ShellResult {
    match spec {
        CommandSpec::Command { program, args } => execute_command(program, args),
        CommandSpec::Pipeline {
            predecessors,
            final_cmd,
        } => execute_pipeline(predecessors, final_cmd),
        CommandSpec::Subshell { runnable } => execute_subshell(runnable),
        CommandSpec::Redirect { runnable, target } => execute_redirect(runnable, target),
    }
}

/// Execute a single command
fn execute_command(program: &str, args: &[String]) -> ShellResult {
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => {
            resolve_and_exec(program, args);
        }
        Err(e) => panic!("fork failed: {}", e),
    }
}

/// Execute a pipeline
fn execute_pipeline(predecessors: &[CommandSpec], final_cmd: &CommandSpec) -> ShellResult {
    run_pipeline(predecessors, final_cmd)
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

/// Execute command with output redirection
fn execute_redirect(spec: &CommandSpec, target: &RedirectTarget) -> ShellResult {
    match unsafe { fork() } {
        Ok(ForkResult::Parent { child }) => wait_for_child(child),
        Ok(ForkResult::Child) => {
            // Set up the output redirection
            match target {
                RedirectTarget::FilePath { path, append } => {
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
                            std::process::exit(1);
                        }
                    }
                }
                RedirectTarget::FileDescriptor { fd } => {
                    // Redirect stdout to the provided file descriptor
                    unsafe {
                        libc::dup2(*fd, 1);
                        // Close the original fd since dup2 created a copy at fd 1
                        libc::close(*fd);
                    }
                }
            }

            // Execute the inner command
            let result = execute(spec);
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

/// Resolve a program name to its full path following POSIX command search rules
///
/// POSIX rules:
/// 1. If program contains '/', use it as a literal path (absolute or relative)
/// 2. Otherwise, search PATH environment variable directories in order
/// 3. Return the first executable file found
fn resolve_program_path(program: &str) -> Result<PathBuf, ProgramResolutionError> {
    // Rule 1: If program contains '/', treat as literal path
    if program.contains('/') {
        let path = PathBuf::from(program);

        // Check if the file exists
        if !path.exists() {
            return Err(ProgramResolutionError::NoSuchFile(format!(
                "{}: No such file or directory",
                program
            )));
        }

        // Check if it's executable (using access syscall)
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            match std::fs::metadata(&path) {
                Ok(metadata) => {
                    let permissions = metadata.permissions();
                    if permissions.mode() & 0o111 == 0 {
                        return Err(ProgramResolutionError::PermissionDenied(format!(
                            "{}: Permission denied",
                            program
                        )));
                    }
                }
                Err(_) => {
                    return Err(ProgramResolutionError::PermissionDenied(format!(
                        "{}: Permission denied",
                        program
                    )));
                }
            }
        }

        return Ok(path);
    }

    // Rule 2: Search PATH environment variable
    // Extract PATH directories, supporting both List and String variants
    let path_dirs: Vec<String> = match get_var("PATH") {
        Some(EnvValue::List(items)) => {
            // PATH is a list - convert items to strings
            let mut dirs = Vec::new();
            for item in items {
                match item {
                    EnvValue::String(s) => dirs.push(s),
                    EnvValue::FilePath(p) => dirs.push(p.to_string_lossy().to_string()),
                    _ => {
                        return Err(ProgramResolutionError::InvalidPath(
                            "PATH list contains invalid values".to_string(),
                        ));
                    }
                }
            }
            dirs
        }
        Some(EnvValue::String(s)) => {
            // PATH is a colon-separated string (traditional format)
            s.split(':').map(String::from).collect()
        }
        Some(EnvValue::FilePath(p)) => {
            // PATH is a single FilePath - treat as single directory
            vec![p.to_string_lossy().to_string()]
        }
        Some(_) => {
            // PATH is set but has invalid type (Integer, Decimal, Bool, None)
            return Err(ProgramResolutionError::InvalidPath(
                "PATH must be a string, path, or list".to_string(),
            ));
        }
        None => {
            // PATH is not set - use a simple default
            vec![
                "/usr/local/bin".to_string(),
                "/usr/bin".to_string(),
                "/bin".to_string(),
            ]
        }
    };

    // Search each directory in PATH
    for dir in &path_dirs {
        if dir.is_empty() {
            continue;
        }

        let candidate = PathBuf::from(dir).join(program);

        // Check if file exists and is executable
        if candidate.exists() {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(metadata) = std::fs::metadata(&candidate) {
                    let permissions = metadata.permissions();
                    if permissions.mode() & 0o111 != 0 {
                        return Ok(candidate);
                    }
                }
            }
        }
    }

    // Command not found in PATH
    Err(ProgramResolutionError::NotFound(format!(
        "{}: command not found",
        program
    )))
}

/// Resolve program path and execute with arguments (never returns on success)
fn resolve_and_exec(program: &str, args: &[String]) -> ! {
    // Resolve the program path using POSIX rules
    let prog_path = match resolve_program_path(program) {
        Ok(path) => path,
        Err(error) => {
            eprintln!("{}", error.message());
            std::process::exit(error.exit_code());
        }
    };

    let prog_path_str = prog_path.to_string_lossy();
    let prog_cstr = CString::new(prog_path_str.as_ref()).expect("Program path contains null byte");

    // Build argv (first arg is the program name as given, not the full path)
    let mut argv: Vec<CString> = Vec::new();
    argv.push(CString::new(program).expect("Program name contains null byte"));
    for arg in args {
        argv.push(CString::new(arg.as_str()).expect("Argument contains null byte"));
    }

    // Get environment
    let env = get_shell_env();
    let env_read = env.read().unwrap();
    let envp = env_read.to_envp();

    // Execute with environment
    let err = execve(&prog_cstr, &argv, &envp);
    eprintln!("Failed to execute {}: {}", program, err.unwrap_err());
    std::process::exit(127);
}

/// Execute a CommandSpec in a pipeline stage (doesn't return on success)
fn exec_pipeline_stage(spec: &CommandSpec) -> ! {
    match spec {
        CommandSpec::Command { program, args } => {
            resolve_and_exec(program, args);
        }
        CommandSpec::Subshell { runnable } => {
            // Execute the subshell and exit with its result
            let result = execute(runnable);
            std::process::exit(result.exit_code as i32);
        }
        CommandSpec::Redirect { .. } => {
            // Execute the redirect and exit with its result
            let result = execute(spec);
            std::process::exit(result.exit_code as i32);
        }
        CommandSpec::Pipeline { .. } => {
            panic!("Nested pipelines are impossible due to operator flattening");
        }
    }
}

/// Execute a pipeline: predecessors → last
fn run_pipeline(predecessors: &[CommandSpec], final_cmd: &CommandSpec) -> ShellResult {
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
    wait_for_child(last_child)
}
