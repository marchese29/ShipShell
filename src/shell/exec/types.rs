use super::super::builtins::get_builtin;

#[derive(Debug, Clone)]
pub struct ShellResult {
    pub exit_code: u8,
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
        }
    }
}
