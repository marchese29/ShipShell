use nix::libc;
use nix::unistd::execve;
use std::collections::HashMap;
use std::ffi::CString;
use std::path::{Path, PathBuf};

use super::super::env::EnvValue;
use super::types::{ExecutionContext, ProgramResolutionError};

/// Resolve program path using env_map (no global state access)
pub fn resolve_program_path(
    program: &str,
    env_map: &HashMap<String, EnvValue>,
) -> Result<PathBuf, ProgramResolutionError> {
    // Rule 1: If contains '/', treat as literal path
    if program.contains('/') {
        let path = PathBuf::from(program);
        if !path.exists() {
            return Err(ProgramResolutionError::NoSuchFile(format!(
                "{}: No such file or directory",
                program
            )));
        }

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if let Ok(metadata) = std::fs::metadata(&path)
                && metadata.permissions().mode() & 0o111 == 0
            {
                return Err(ProgramResolutionError::PermissionDenied(format!(
                    "{}: Permission denied",
                    program
                )));
            }
        }

        return Ok(path);
    }

    // Rule 2: Search PATH from env_map
    let path_dirs: Vec<String> = match env_map.get("PATH") {
        Some(EnvValue::List(items)) => items
            .iter()
            .filter_map(|item| match item {
                EnvValue::String(s) => Some(s.clone()),
                EnvValue::FilePath(p) => Some(p.to_string_lossy().to_string()),
                _ => None,
            })
            .collect(),
        Some(EnvValue::String(s)) => s.split(':').map(String::from).collect(),
        Some(EnvValue::FilePath(p)) => vec![p.to_string_lossy().to_string()],
        _ => {
            // Default PATH
            vec![
                "/usr/local/bin".to_string(),
                "/usr/bin".to_string(),
                "/bin".to_string(),
            ]
        }
    };

    // Search each directory
    for dir in &path_dirs {
        if dir.is_empty() {
            continue;
        }

        let candidate = PathBuf::from(dir).join(program);
        if candidate.exists() {
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                if let Ok(metadata) = std::fs::metadata(&candidate)
                    && metadata.permissions().mode() & 0o111 != 0
                {
                    return Ok(candidate);
                }
            }
        }
    }

    Err(ProgramResolutionError::NotFound(format!(
        "{}: command not found",
        program
    )))
}

/// Execute already-resolved program (no locks, just execve)
pub fn exec_resolved(
    program_name: &str,
    resolved_path: &Path,
    args: &[String],
    context: ExecutionContext,
) -> ! {
    // Apply capture if needed
    if let Some(capture) = &context.capture {
        unsafe {
            libc::dup2(capture.stdout_pipe, 1);
            libc::dup2(capture.stderr_pipe, 2);
            libc::close(capture.stdout_pipe);
            libc::close(capture.stderr_pipe);
        }
    }

    // Apply all redirects (these override capture)
    for redirect in &context.redirects {
        apply_redirect(redirect);
    }

    // Build argv
    let prog_path_str = resolved_path.to_string_lossy();
    let prog_cstr = CString::new(prog_path_str.as_ref()).expect("Program path contains null byte");

    let mut argv: Vec<CString> = Vec::new();
    argv.push(CString::new(program_name).expect("Program name contains null byte"));
    for arg in args {
        argv.push(CString::new(arg.as_str()).expect("Argument contains null byte"));
    }

    // Convert env_map to envp
    let envp: Vec<CString> = context
        .env_vars
        .iter()
        .filter_map(|(key, value)| {
            let value_str = value.to_string_repr();
            CString::new(format!("{}={}", key, value_str)).ok()
        })
        .collect();

    // Execute
    let err = execve(&prog_cstr, &argv, &envp);
    eprintln!("Failed to execute {}: {}", program_name, err.unwrap_err());
    std::process::exit(127);
}

fn apply_redirect(redirect: &super::types::RedirectOperation) {
    match redirect {
        super::types::RedirectOperation::FileOutput { path, append, fd } => {
            use std::fs::OpenOptions;
            let file = OpenOptions::new()
                .write(true)
                .create(true)
                .truncate(!append)
                .append(*append)
                .open(path)
                .unwrap_or_else(|e| {
                    eprintln!("{}: {}", path, e);
                    std::process::exit(1);
                });

            use std::os::unix::io::IntoRawFd;
            let file_fd = file.into_raw_fd();
            unsafe {
                libc::dup2(file_fd, *fd);
                libc::close(file_fd);
            }
        }
        super::types::RedirectOperation::FileDescriptor { from_fd, to_fd } => unsafe {
            libc::dup2(*from_fd, *to_fd);
            libc::close(*from_fd);
        },
    }
}
