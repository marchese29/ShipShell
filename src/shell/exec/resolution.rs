use nix::unistd::execve;
use std::ffi::CString;
use std::path::PathBuf;

use super::super::env::{EnvValue, get_shell_env, get_var};
use super::types::ProgramResolutionError;

/// Resolve program path and execute with arguments (never returns on success)
pub fn resolve_and_exec(program: &str, args: &[String]) -> ! {
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
