use std::env;
use std::path::PathBuf;

use super::env::{EnvValue, get_shell_env, get_var};

/// Get a builtin function by name
///
/// Returns Some(function) if the name corresponds to a builtin, None otherwise.
/// This serves as both the builtin registry and dispatcher.
pub fn get_builtin(name: &str) -> Option<fn(&[String]) -> i32> {
    match name {
        "cd" => Some(cd),
        "pwd" => Some(pwd),
        "pushd" => Some(pushd),
        "popd" => Some(popd),
        "dirs" => Some(dirs),
        "exit" => Some(exit_builtin),
        "quit" => Some(quit),
        _ => None,
    }
}

/// Change the current working directory
///
/// Args:
///   - [] -> change to HOME
///   - ["-"] -> change to OLDPWD
///   - [path] -> change to path
pub fn cd(args: &[String]) -> i32 {
    // Determine target directory
    let target = if args.is_empty() {
        // No argument - go to HOME
        match get_var("HOME") {
            Some(EnvValue::String(s)) => PathBuf::from(s),
            Some(EnvValue::FilePath(p)) => p,
            _ => {
                eprintln!("cd: HOME not set");
                return 1;
            }
        }
    } else if args[0] == "-" {
        // cd - (change to previous directory)
        match get_var("OLDPWD") {
            Some(EnvValue::String(s)) => {
                println!("{}", s);
                PathBuf::from(s)
            }
            Some(EnvValue::FilePath(p)) => {
                println!("{}", p.display());
                p.clone()
            }
            _ => {
                eprintln!("cd: OLDPWD not set");
                return 1;
            }
        }
    } else {
        // Specific path provided
        let path_str = &args[0];

        // Expand tilde if present
        if path_str.starts_with('~') {
            match get_var("HOME") {
                Some(EnvValue::String(s)) => {
                    if path_str == "~" {
                        PathBuf::from(&s)
                    } else if let Some(stripped) = path_str.strip_prefix("~/") {
                        PathBuf::from(&s).join(stripped)
                    } else {
                        // ~user syntax - just treat as literal for now
                        PathBuf::from(path_str)
                    }
                }
                Some(EnvValue::FilePath(p)) => {
                    if path_str == "~" {
                        p
                    } else if let Some(stripped) = path_str.strip_prefix("~/") {
                        p.join(stripped)
                    } else {
                        // TODO: Handle ~user syntax
                        PathBuf::from(path_str)
                    }
                }
                _ => {
                    eprintln!("cd: HOME not set");
                    return 1;
                }
            }
        } else {
            PathBuf::from(path_str)
        }
    };

    // Store current directory as OLDPWD before changing
    let current_dir = match env::current_dir() {
        Ok(dir) => dir,
        Err(e) => {
            eprintln!("cd: cannot get current directory: {}", e);
            return 1;
        }
    };

    // Change directory
    if let Err(e) = env::set_current_dir(&target) {
        eprintln!("cd: {}: {}", target.display(), e);
        return 1;
    }

    // Get the new current directory (after successful change)
    let new_dir = match env::current_dir() {
        Ok(dir) => dir,
        Err(e) => {
            eprintln!("cd: cannot get new directory: {}", e);
            return 1;
        }
    };

    // Update environment variables
    let env = get_shell_env();
    let mut env_write = env.write().unwrap();
    env_write.set("OLDPWD".to_string(), EnvValue::FilePath(current_dir));
    env_write.set("PWD".to_string(), EnvValue::FilePath(new_dir));

    0
}

/// Print the current working directory
///
/// Args:
///   - [] -> print logical path (from PWD)
///   - ["-P"] -> print physical path (resolve symlinks)
pub fn pwd(args: &[String]) -> i32 {
    let physical = args.iter().any(|arg| arg == "-P");

    let result = if physical {
        // Physical path: resolve all symlinks
        match env::current_dir() {
            Ok(dir) => dir,
            Err(e) => {
                eprintln!("pwd: {}", e);
                return 1;
            }
        }
    } else {
        // Logical path: get from shell environment
        let env = get_shell_env();
        let env_read = env.read().unwrap();
        match env_read.get("PWD") {
            Some(EnvValue::FilePath(p)) => p.clone(),
            Some(EnvValue::String(s)) => PathBuf::from(s),
            _ => {
                // Fallback to physical path if PWD not set
                match env::current_dir() {
                    Ok(dir) => dir,
                    Err(e) => {
                        eprintln!("pwd: {}", e);
                        return 1;
                    }
                }
            }
        }
    };

    println!("{}", result.display());
    0
}

/// Push a directory onto the directory stack and change to it
///
/// Args:
///   - [path] -> directory to change to
pub fn pushd(args: &[String]) -> i32 {
    if args.is_empty() {
        eprintln!("pushd: no directory specified");
        return 1;
    }

    // Get current directory before changing
    let current_dir = match env::current_dir() {
        Ok(dir) => dir,
        Err(e) => {
            eprintln!("pushd: cannot get current directory: {}", e);
            return 1;
        }
    };

    // Push current directory onto stack
    let env = get_shell_env();
    let mut env_write = env.write().unwrap();
    env_write.push_dir(current_dir);
    drop(env_write); // Release the lock before calling cd

    // Change to the new directory
    let exit_code = cd(args);

    if exit_code == 0 {
        // Print the new directory
        if let Ok(new_dir) = env::current_dir() {
            println!("{}", new_dir.display());
        }
    }

    exit_code
}

/// Pop a directory from the directory stack and change to it
///
/// Args: none
pub fn popd(args: &[String]) -> i32 {
    if !args.is_empty() {
        eprintln!("popd: too many arguments");
        return 1;
    }

    // Pop from directory stack
    let env = get_shell_env();
    let mut env_write = env.write().unwrap();
    let target = match env_write.pop_dir() {
        Some(dir) => dir,
        None => {
            eprintln!("popd: directory stack empty");
            return 1;
        }
    };
    drop(env_write); // Release the lock before calling cd

    // Change to the popped directory
    let target_str = target.to_string_lossy().to_string();
    let exit_code = cd(&[target_str]);

    if exit_code == 0 {
        // Print the new directory
        if let Ok(new_dir) = env::current_dir() {
            println!("{}", new_dir.display());
        }
    }

    exit_code
}

/// Display the directory stack
///
/// Args: none
pub fn dirs(args: &[String]) -> i32 {
    if !args.is_empty() {
        eprintln!("dirs: too many arguments");
        return 1;
    }

    // Get current directory
    let current_dir = match env::current_dir() {
        Ok(dir) => dir,
        Err(e) => {
            eprintln!("dirs: {}", e);
            return 1;
        }
    };

    // Print current directory first
    println!("{}", current_dir.display());

    // Print directory stack
    let env = get_shell_env();
    let env_read = env.read().unwrap();
    for dir in env_read.dir_stack() {
        println!("{}", dir.display());
    }

    0
}

/// Exit the shell
///
/// Args:
///   - [] -> exit with code 0
///   - [code] -> exit with specified code
pub fn exit_builtin(args: &[String]) -> i32 {
    let exit_code = if args.is_empty() {
        0
    } else {
        args[0].parse::<i32>().unwrap_or(1)
    };

    std::process::exit(exit_code);
}

/// Quit the shell (alias for exit)
///
/// Args:
///   - [] -> exit with code 0
///   - [code] -> exit with specified code
pub fn quit(args: &[String]) -> i32 {
    exit_builtin(args)
}
