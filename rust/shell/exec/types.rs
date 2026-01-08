use super::super::builtins::get_builtin;
use super::super::env::EnvValue;
use std::collections::HashMap;

#[derive(Debug, Clone)]
pub enum ShellResult {
    ExitOnly {
        exit_code: u8,
    },
    Captured {
        exit_code: u8,
        stdout_fd: i32,
        stderr_fd: i32,
    },
}

impl ShellResult {
    /// Get the exit code regardless of variant
    pub fn exit_code(&self) -> u8 {
        match self {
            ShellResult::ExitOnly { exit_code } => *exit_code,
            ShellResult::Captured { exit_code, .. } => *exit_code,
        }
    }
}

/// Public interface for executing commands from Python bindings
/// This enum hides shell internals (like builtin detection) from the bindings layer
#[derive(Debug, Clone)]
pub enum ExecRequest {
    Program {
        name: String,
        args: Vec<String>,
    },
    Pipeline {
        stages: Vec<ExecRequest>,
    },
    Subshell {
        request: Box<ExecRequest>,
    },
    Redirect {
        request: Box<ExecRequest>,
        target: RedirectTarget,
    },
    WithEnv {
        request: Box<ExecRequest>,
        env_overlay: HashMap<String, EnvValue>,
    },
    ScriptExec {
        code: String,
    },
    Negated {
        request: Box<ExecRequest>,
    },
}

/// Represents errors that can occur during program path resolution
#[derive(Debug)]
pub enum ProgramResolutionError {
    /// Command not found in PATH
    NotFound(String),
    /// File doesn't exist (for paths with '/')
    NoSuchFile(String),
    /// File exists but is not executable
    PermissionDenied(String),
    /// PATH environment variable has invalid configuration
    #[allow(dead_code)]
    InvalidPath(String),
}

impl ProgramResolutionError {
    /// Get the appropriate exit code for this error type
    pub fn exit_code(&self) -> i32 {
        match self {
            ProgramResolutionError::NotFound(_) => 127,
            ProgramResolutionError::NoSuchFile(_) => 127,
            ProgramResolutionError::PermissionDenied(_) => 126,
            ProgramResolutionError::InvalidPath(_) => 127,
        }
    }

    /// Get the error message
    pub fn message(&self) -> &str {
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

/// Redirect operation to be applied in child process before exec
#[derive(Clone, Debug)]
pub enum RedirectOperation {
    FileOutput { path: String, append: bool, fd: i32 },
    FileDescriptor { from_fd: i32, to_fd: i32 },
}

/// Capture information for stdout/stderr
#[derive(Clone, Debug)]
pub struct CaptureInfo {
    pub stdout_pipe: i32,
    pub stderr_pipe: i32,
}

/// Execution context passed through the execution tree
/// Contains all parameters needed for execution without reading global state
#[derive(Clone)]
pub struct ExecutionContext {
    /// Environment variables to pass to child processes
    pub env_vars: HashMap<String, EnvValue>,

    /// Pending redirects to apply in child before exec
    pub redirects: Vec<RedirectOperation>,

    /// Optional capture mode for stdout/stderr
    pub capture: Option<CaptureInfo>,
}

impl ExecutionContext {
    /// Create new context with environment variables
    pub fn new(env_vars: HashMap<String, EnvValue>) -> Self {
        Self {
            env_vars,
            redirects: Vec::new(),
            capture: None,
        }
    }

    /// Add a redirect operation to the context
    pub fn with_redirect(mut self, redirect: RedirectOperation) -> Self {
        self.redirects.push(redirect);
        self
    }

    /// Merge environment overlay into context
    pub fn with_env_overlay(mut self, overlay: &HashMap<String, EnvValue>) -> Self {
        for (key, value) in overlay {
            self.env_vars.insert(key.clone(), value.clone());
        }
        self
    }

    /// Set capture mode for this context
    pub fn with_capture(mut self, stdout_fd: i32, stderr_fd: i32) -> Self {
        self.capture = Some(CaptureInfo {
            stdout_pipe: stdout_fd,
            stderr_pipe: stderr_fd,
        });
        self
    }
}

impl std::fmt::Debug for ExecutionContext {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ExecutionContext")
            .field("env_vars", &format!("{} vars", self.env_vars.len()))
            .field("redirects", &self.redirects)
            .field("capture", &self.capture)
            .finish()
    }
}

#[derive(Clone)]
pub enum CommandSpec {
    Command {
        program: String,
        args: Vec<String>,
    },
    Builtin {
        name: String,               // For debugging/logging
        func: fn(&[String]) -> i32, // Function pointer for efficient execution
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
    WithEnv {
        runnable: Box<CommandSpec>,
        env_overlay: HashMap<String, EnvValue>,
    },
    ScriptExec {
        code: String,
    },
    Negated {
        runnable: Box<CommandSpec>,
    },
}

// Custom Debug impl since function pointers don't implement Debug
impl std::fmt::Debug for CommandSpec {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match self {
            CommandSpec::Command { program, args } => f
                .debug_struct("Command")
                .field("program", program)
                .field("args", args)
                .finish(),
            CommandSpec::Builtin { name, args, .. } => f
                .debug_struct("Builtin")
                .field("name", name)
                .field("args", args)
                .finish(),
            CommandSpec::Pipeline {
                predecessors,
                final_cmd,
            } => f
                .debug_struct("Pipeline")
                .field("predecessors", predecessors)
                .field("final_cmd", final_cmd)
                .finish(),
            CommandSpec::Subshell { runnable } => f
                .debug_struct("Subshell")
                .field("runnable", runnable)
                .finish(),
            CommandSpec::Redirect { runnable, target } => f
                .debug_struct("Redirect")
                .field("runnable", runnable)
                .field("target", target)
                .finish(),
            CommandSpec::WithEnv {
                runnable,
                env_overlay,
            } => f
                .debug_struct("WithEnv")
                .field("runnable", runnable)
                .field("env_overlay", env_overlay)
                .finish(),
            CommandSpec::ScriptExec { code } => f
                .debug_struct("ScriptExec")
                .field(
                    "code",
                    &format!("{}...", &code.chars().take(50).collect::<String>()),
                )
                .finish(),
            CommandSpec::Negated { runnable } => f
                .debug_struct("Negated")
                .field("runnable", runnable)
                .finish(),
        }
    }
}

/// Convert an ExecRequest to a CommandSpec with builtin resolution
impl From<&ExecRequest> for CommandSpec {
    fn from(request: &ExecRequest) -> Self {
        match request {
            ExecRequest::Program { name, args } => {
                // Check if it's a builtin using get_builtin()
                if let Some(func) = get_builtin(name) {
                    CommandSpec::Builtin {
                        name: name.clone(),
                        func,
                        args: args.clone(),
                    }
                } else {
                    CommandSpec::Command {
                        program: name.clone(),
                        args: args.clone(),
                    }
                }
            }
            ExecRequest::Pipeline { stages } => {
                // Convert all stages recursively
                let specs: Vec<CommandSpec> = stages.iter().map(CommandSpec::from).collect();

                // Split into predecessors and final command
                let mut specs_iter = specs.into_iter();
                let first = specs_iter
                    .next()
                    .expect("Pipeline must have at least one stage");
                let rest: Vec<CommandSpec> = specs_iter.collect();

                if rest.is_empty() {
                    // Single-stage pipeline is just the command itself
                    first
                } else {
                    // Multi-stage pipeline
                    let mut predecessors = vec![first];
                    predecessors.extend(rest[..rest.len() - 1].iter().cloned());
                    let final_cmd = rest.last().unwrap().clone();

                    CommandSpec::Pipeline {
                        predecessors,
                        final_cmd: Box::new(final_cmd),
                    }
                }
            }
            ExecRequest::Subshell { request } => CommandSpec::Subshell {
                runnable: Box::new(CommandSpec::from(request.as_ref())),
            },
            ExecRequest::Redirect { request, target } => CommandSpec::Redirect {
                runnable: Box::new(CommandSpec::from(request.as_ref())),
                target: target.clone(),
            },
            ExecRequest::WithEnv {
                request,
                env_overlay,
            } => CommandSpec::WithEnv {
                runnable: Box::new(CommandSpec::from(request.as_ref())),
                env_overlay: env_overlay.clone(),
            },
            ExecRequest::ScriptExec { code } => CommandSpec::ScriptExec { code: code.clone() },
            ExecRequest::Negated { request } => CommandSpec::Negated {
                runnable: Box::new(CommandSpec::from(request.as_ref())),
            },
        }
    }
}
