use nix::libc;
use pyo3::exceptions::PyKeyError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;
use std::ffi::CString;
use std::fs::File;
use std::io::Read;
use std::os::unix::io::FromRawFd;
use std::sync::Arc;

use crate::shell::exec::{ShellResult, execute_with_capture};
use crate::shell::{self, EnvValue, ExecRequest, execute};

/// Execute Python code (for ScriptExec)
/// This is used by the shell execution system to run Python scripts
pub fn execute_python_code(code: &str) -> anyhow::Result<()> {
    pyo3::Python::attach(|py| {
        let code_cstr = CString::new(code)?;
        py.run(code_cstr.as_c_str(), None, None)?;
        Ok(())
    })
}

/// Convert a Python object to an EnvValue with strict type checking (no coercion)
fn py_to_env_value(obj: &Bound<PyAny>) -> PyResult<EnvValue> {
    use pyo3::types::{PyBool, PyFloat, PyInt, PyString};
    use std::path::PathBuf;

    // Check for None first
    if obj.is_none() {
        return Ok(EnvValue::None);
    }

    // Check for bool BEFORE int (bool is subclass of int in Python!)
    if obj.is_instance_of::<PyBool>() {
        return Ok(EnvValue::Bool(obj.extract::<bool>()?));
    }

    // Check for int (but not bool, which we already handled)
    if obj.is_instance_of::<PyInt>() {
        return Ok(EnvValue::Integer(obj.extract::<i64>()?));
    }

    // Check for float
    if obj.is_instance_of::<PyFloat>() {
        return Ok(EnvValue::Decimal(obj.extract::<f64>()?));
    }

    // Check for string
    if obj.is_instance_of::<PyString>() {
        return Ok(EnvValue::String(obj.extract::<String>()?));
    }

    // Check for pathlib.Path
    let py = obj.py();
    if let Ok(pathlib) = py.import("pathlib")
        && let Ok(path_class) = pathlib.getattr("Path")
        && obj.is_instance(&path_class)?
    {
        let path_str: String = obj.call_method0("__str__")?.extract()?;
        return Ok(EnvValue::FilePath(PathBuf::from(path_str)));
    }

    // Check for list
    if let Ok(list) = obj.cast::<PyList>() {
        let mut vec = Vec::new();
        for item in list.iter() {
            vec.push(py_to_env_value(&item)?);
        }
        return Ok(EnvValue::List(vec));
    }

    Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
        "Value must be str, int, float, bool, None, Path, or list - no coercion allowed",
    ))
}

/// Convert an EnvValue to a Python object
fn env_value_to_py(py: Python, value: &EnvValue) -> PyResult<Py<PyAny>> {
    match value {
        EnvValue::String(s) => Ok(s.clone().into_pyobject(py)?.into_any().unbind()),
        EnvValue::Integer(i) => Ok((*i).into_pyobject(py)?.into_any().unbind()),
        EnvValue::Decimal(f) => Ok((*f).into_pyobject(py)?.into_any().unbind()),
        EnvValue::Bool(b) => Ok((*b).into_pyobject(py)?.to_owned().into_any().unbind()),
        EnvValue::None => Ok(py.None()),
        EnvValue::List(vec) => {
            let items: Result<Vec<Py<PyAny>>, _> =
                vec.iter().map(|item| env_value_to_py(py, item)).collect();
            Ok(PyList::new(py, &items?)?.into_any().unbind())
        }
        EnvValue::FilePath(path) => {
            // Import pathlib.Path and create a Path object
            let pathlib = py.import("pathlib")?;
            let path_class = pathlib.getattr("Path")?;
            let path_str = path.to_string_lossy().to_string();
            let path_obj = path_class.call1((path_str,))?;
            Ok(path_obj.unbind())
        }
    }
}

#[pyclass]
#[derive(Clone)]
pub struct ShipProgram {
    name: String,
}

impl ShipProgram {
    pub fn name(&self) -> &str {
        &self.name
    }
}

#[pymethods]
impl ShipProgram {
    #[pyo3(signature = (*args))]
    fn __call__(&self, args: Vec<String>) -> PyResult<ShipRunnable> {
        Ok(ShipRunnable(Arc::new(Runnable::Command {
            prog: self.clone(),
            args,
        })))
    }
}

#[pyclass(frozen)]
#[derive(Clone)]
pub struct ShipRunnable(Arc<Runnable>);

#[allow(dead_code)]
#[derive(Clone)]
enum Runnable {
    Command {
        prog: ShipProgram,
        args: Vec<String>,
    },
    Pipeline {
        predecessors: Vec<ShipRunnable>,
        final_cmd: ShipRunnable,
    },
    Subshell {
        runnable: ShipRunnable,
    },
    Redirect {
        runnable: ShipRunnable,
        target: RedirectTarget,
    },
    WithEnv {
        runnable: ShipRunnable,
        env_overlay: HashMap<String, EnvValue>,
    },
    ScriptExec {
        code: String,
    },
    Negated {
        runnable: ShipRunnable,
    },
}

#[derive(Clone)]
enum RedirectTarget {
    FilePath { path: String, append: bool },
    FileDescriptor { fd: i32 },
}

#[pyclass]
#[derive(Clone)]
pub struct ShipResult {
    #[pyo3(get)]
    pub exit_code: u8,
}

impl From<&ShipRunnable> for ExecRequest {
    fn from(runnable: &ShipRunnable) -> Self {
        match runnable.0.as_ref() {
            Runnable::Command { prog, args } => ExecRequest::Program {
                name: prog.name().to_string(),
                args: args.clone(),
            },
            Runnable::Pipeline {
                predecessors,
                final_cmd,
            } => {
                let mut stages: Vec<ExecRequest> = predecessors.iter().map(|p| p.into()).collect();
                stages.push(final_cmd.into());
                ExecRequest::Pipeline { stages }
            }
            Runnable::Subshell { runnable } => ExecRequest::Subshell {
                request: Box::new(runnable.into()),
            },
            Runnable::Redirect { runnable, target } => {
                let shell_target = match target {
                    RedirectTarget::FilePath { path, append } => shell::RedirectTarget::FilePath {
                        path: path.clone(),
                        append: *append,
                    },
                    RedirectTarget::FileDescriptor { fd } => {
                        shell::RedirectTarget::FileDescriptor { fd: *fd }
                    }
                };
                ExecRequest::Redirect {
                    request: Box::new(runnable.into()),
                    target: shell_target,
                }
            }
            Runnable::WithEnv {
                runnable,
                env_overlay,
            } => ExecRequest::WithEnv {
                request: Box::new(runnable.into()),
                env_overlay: env_overlay.clone(),
            },
            Runnable::ScriptExec { code } => ExecRequest::ScriptExec { code: code.clone() },
            Runnable::Negated { runnable } => ExecRequest::Negated {
                request: Box::new(runnable.into()),
            },
        }
    }
}

#[pymethods]
impl ShipRunnable {
    fn __or__(&self, other: &ShipRunnable) -> PyResult<ShipRunnable> {
        use Runnable::*;

        let result_inner = match (self.0.as_ref(), other.0.as_ref()) {
            // Redirect on either side - error (redirections can't be piped)
            (Redirect { .. }, _) => {
                return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                    "Cannot pipe from a redirected command - redirection must be the final operation",
                ));
            }
            (_, Redirect { .. }) => {
                return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                    "Cannot pipe to a redirected command - redirection must be the final operation",
                ));
            }

            // Atomic | Atomic -> Pipeline([lhs], rhs)
            // (Command, Subshell, WithEnv, ScriptExec, and Negated are all atomic units)
            (
                Command { .. }
                | Subshell { .. }
                | WithEnv { .. }
                | ScriptExec { .. }
                | Negated { .. },
                Command { .. }
                | Subshell { .. }
                | WithEnv { .. }
                | ScriptExec { .. }
                | Negated { .. },
            ) => Arc::new(Pipeline {
                predecessors: vec![self.clone()],
                final_cmd: other.clone(),
            }),

            // Pipeline | Atomic -> extend pipeline
            (
                Pipeline {
                    predecessors,
                    final_cmd,
                },
                Command { .. }
                | Subshell { .. }
                | WithEnv { .. }
                | ScriptExec { .. }
                | Negated { .. },
            ) => {
                let mut new_predecessors = predecessors.clone();
                new_predecessors.push(final_cmd.clone());
                Arc::new(Pipeline {
                    predecessors: new_predecessors,
                    final_cmd: other.clone(),
                })
            }

            // Atomic | Pipeline -> prepend to pipeline
            (
                Command { .. }
                | Subshell { .. }
                | WithEnv { .. }
                | ScriptExec { .. }
                | Negated { .. },
                Pipeline {
                    predecessors,
                    final_cmd,
                },
            ) => {
                let mut new_predecessors = vec![self.clone()];
                new_predecessors.extend(predecessors.clone());
                Arc::new(Pipeline {
                    predecessors: new_predecessors,
                    final_cmd: final_cmd.clone(),
                })
            }

            // Pipeline | Pipeline -> flatten both
            (
                Pipeline {
                    predecessors: lhs_preds,
                    final_cmd: lhs_final,
                },
                Pipeline {
                    predecessors: rhs_preds,
                    final_cmd: rhs_final,
                },
            ) => {
                let mut new_predecessors = lhs_preds.clone();
                new_predecessors.push(lhs_final.clone());
                new_predecessors.extend(rhs_preds.clone());
                Arc::new(Pipeline {
                    predecessors: new_predecessors,
                    final_cmd: rhs_final.clone(),
                })
            }
        };

        Ok(ShipRunnable(result_inner))
    }

    fn __call__(&self) -> PyResult<ShipResult> {
        let result = execute(&self.into());
        Ok(ShipResult {
            exit_code: result.exit_code(),
        })
    }

    fn __gt__(&self, target: Bound<PyAny>) -> PyResult<ShipRunnable> {
        let redirect_target = if let Ok(path) = target.extract::<String>() {
            // String path - truncate mode
            RedirectTarget::FilePath {
                path,
                append: false,
            }
        } else if target.hasattr("fileno")? {
            // File-like object - get file descriptor
            let fileno_method = target.getattr("fileno")?;
            let fd: i32 = fileno_method.call0()?.extract()?;

            // Duplicate the file descriptor for cross-fork safety
            let dup_fd = unsafe { libc::dup(fd) };
            if dup_fd == -1 {
                return Err(PyErr::new::<pyo3::exceptions::PyOSError, _>(
                    "Failed to duplicate file descriptor",
                ));
            }

            RedirectTarget::FileDescriptor { fd: dup_fd }
        } else {
            return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                "Redirect target must be a string path or file-like object with fileno()",
            ));
        };

        Ok(ShipRunnable(Arc::new(Runnable::Redirect {
            runnable: self.clone(),
            target: redirect_target,
        })))
    }

    fn __rshift__(&self, target: Bound<PyAny>) -> PyResult<ShipRunnable> {
        let redirect_target = if let Ok(path) = target.extract::<String>() {
            // String path - append mode
            RedirectTarget::FilePath { path, append: true }
        } else if target.hasattr("fileno")? {
            // File-like object - get file descriptor
            let fileno_method = target.getattr("fileno")?;
            let fd: i32 = fileno_method.call0()?.extract()?;

            // Duplicate the file descriptor for cross-fork safety
            let dup_fd = unsafe { libc::dup(fd) };
            if dup_fd == -1 {
                return Err(PyErr::new::<pyo3::exceptions::PyOSError, _>(
                    "Failed to duplicate file descriptor",
                ));
            }

            RedirectTarget::FileDescriptor { fd: dup_fd }
        } else {
            return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                "Redirect target must be a string path or file-like object with fileno()",
            ));
        };

        Ok(ShipRunnable(Arc::new(Runnable::Redirect {
            runnable: self.clone(),
            target: redirect_target,
        })))
    }

    /// Apply environment overlay to this runnable
    ///
    /// Usage:
    ///   prog('echo')('Hello').with_env(DEBUG='1', PATH='/custom/path')()
    ///   prog('myapp').with_env(**env_dict)()
    #[pyo3(signature = (**kwargs))]
    fn with_env(&self, kwargs: Option<Bound<PyDict>>) -> PyResult<ShipRunnable> {
        let kwargs = kwargs.ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyTypeError, _>("with_env() requires keyword arguments")
        })?;

        // Convert **kwargs to HashMap<String, EnvValue>
        let mut overlay = HashMap::new();
        for (key, value) in kwargs.iter() {
            let key_str: String = key.extract()?;
            let env_value = py_to_env_value(&value)?;
            overlay.insert(key_str, env_value);
        }

        // Check if we're already a WithEnv - if so, merge overlays
        // New overlay takes precedence over existing overlay
        if let Runnable::WithEnv {
            runnable,
            env_overlay: existing,
        } = self.0.as_ref()
        {
            let mut merged = existing.clone();
            merged.extend(overlay); // New values override old ones
            Ok(ShipRunnable(Arc::new(Runnable::WithEnv {
                runnable: runnable.clone(),
                env_overlay: merged,
            })))
        } else {
            // Wrap this runnable in WithEnv
            Ok(ShipRunnable(Arc::new(Runnable::WithEnv {
                runnable: self.clone(),
                env_overlay: overlay,
            })))
        }
    }

    /// Negate the exit code of this runnable (0 becomes 1, non-zero becomes 0)
    ///
    /// Usage:
    ///   prog('test')('-f', 'file.txt').negated()()  # succeeds if file doesn't exist
    ///   negate(prog('grep')('pattern', 'file.txt'))()  # succeeds if pattern not found
    fn negated(&self) -> PyResult<ShipRunnable> {
        Ok(ShipRunnable(Arc::new(Runnable::Negated {
            runnable: self.clone(),
        })))
    }
}

#[pyfunction]
#[pyo3(signature = (name))]
pub fn prog(name: String) -> PyResult<ShipProgram> {
    // TODO: Resolve the program from the shell environment
    Ok(ShipProgram { name })
}

#[pyfunction]
#[pyo3(signature = (prog, *args))]
pub fn cmd(prog: ShipProgram, args: Vec<String>) -> PyResult<ShipRunnable> {
    // PyO3 automatically converts:
    // - cmd to String (calls __str__ if needed)
    // - each arg to String (calls __str__ if needed)
    Ok(ShipRunnable(Arc::new(Runnable::Command { prog, args })))
}

#[pyfunction]
#[pyo3(signature = (cmd1, cmd2, *cmds))]
pub fn pipe(
    cmd1: ShipRunnable,
    cmd2: ShipRunnable,
    cmds: Vec<ShipRunnable>,
) -> PyResult<ShipRunnable> {
    let mut result = cmd1.__or__(&cmd2)?;
    for cmd in cmds {
        result = result.__or__(&cmd)?;
    }

    Ok(result)
}

#[pyfunction]
pub fn sub(runnable: ShipRunnable) -> PyResult<ShipRunnable> {
    Ok(ShipRunnable(Arc::new(Runnable::Subshell { runnable })))
}

/// Negate a runnable's exit code (convenience function)
///
/// This is equivalent to calling .negated() on a runnable.
///
/// Usage:
///   negate(prog('test')('-f', 'file.txt'))()  # succeeds if file doesn't exist
#[pyfunction]
pub fn negate(runnable: ShipRunnable) -> PyResult<ShipRunnable> {
    runnable.negated()
}

/// Execute a Python script file in the embedded interpreter (in a subshell)
///
/// This is like the shell's "exec" builtin - runs the script with access to
/// shell functionality (shp, prog, env, etc.) but in an isolated namespace.
///
/// Usage:
///   shpexec('script.py')()
///   shpexec('script.py', 'arg1', 'arg2')()
///   shpexec('gen_data.py')() | prog('grep')('pattern')
#[pyfunction]
#[pyo3(signature = (file, *args))]
pub fn shpexec(file: Bound<PyAny>, args: Vec<String>) -> PyResult<ShipRunnable> {
    use std::path::PathBuf;

    // Extract file path from various input types (str, Path, or file-like object)
    let path_str = if let Ok(s) = file.extract::<String>() {
        s
    } else if file.hasattr("__fspath__")? {
        // pathlib.Path or similar
        let path_method = file.getattr("__fspath__")?;
        path_method.call0()?.extract::<String>()?
    } else if file.hasattr("name")? {
        // File-like object with a name attribute
        file.getattr("name")?.extract::<String>()?
    } else {
        return Err(PyErr::new::<pyo3::exceptions::PyTypeError, _>(
            "file must be a string path, Path object, or file-like object with a name",
        ));
    };

    // Expand ~ to home directory if needed
    let path_str = if path_str.starts_with('~') {
        if let Some(home_val) = shell::get_var("HOME") {
            let home = match home_val {
                EnvValue::String(s) => s,
                EnvValue::FilePath(p) => p.to_string_lossy().to_string(),
                _ => path_str.clone(),
            };
            if path_str == "~" {
                home
            } else if let Some(rest) = path_str.strip_prefix("~/") {
                format!("{}/{}", home, rest)
            } else {
                path_str
            }
        } else {
            path_str
        }
    } else {
        path_str
    };

    // Resolve to absolute path using shell's PWD
    let path = PathBuf::from(&path_str);
    let abs_path = if path.is_absolute() {
        path
    } else {
        // Get current directory from shell environment
        let cwd = if let Some(pwd_val) = shell::get_var("PWD") {
            match pwd_val {
                EnvValue::FilePath(p) => p,
                EnvValue::String(s) => PathBuf::from(s),
                _ => std::env::current_dir().map_err(|e| {
                    PyErr::new::<pyo3::exceptions::PyOSError, _>(format!(
                        "Failed to get current directory: {}",
                        e
                    ))
                })?,
            }
        } else {
            std::env::current_dir().map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyOSError, _>(format!(
                    "Failed to get current directory: {}",
                    e
                ))
            })?
        };
        cwd.join(path)
    };

    let abs_path = abs_path.canonicalize().map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyFileNotFoundError, _>(format!(
            "Cannot find file '{}': {}",
            path_str, e
        ))
    })?;

    let abs_path_str = abs_path.to_string_lossy().to_string();

    // Read the Python script
    let script_code = std::fs::read_to_string(&abs_path).map_err(|e| {
        PyErr::new::<pyo3::exceptions::PyIOError, _>(format!(
            "Failed to read '{}': {}",
            abs_path_str, e
        ))
    })?;

    // Build sys.argv list
    let mut argv = vec![abs_path_str.clone()];
    argv.extend(args);

    // Create Python code that sets up environment and executes the script
    let wrapper_code = format!(
        r#"import sys
sys.argv = {argv:?}

# Execute script with proper __name__ and __file__
exec(compile({script_code:?}, {abs_path_str:?}, 'exec'), {{'__name__': '__main__', '__file__': {abs_path_str:?}}})"#
    );

    // Create ScriptExec runnable wrapped in a Subshell for isolation
    let script_exec = ShipRunnable(Arc::new(Runnable::ScriptExec { code: wrapper_code }));

    Ok(ShipRunnable(Arc::new(Runnable::Subshell {
        runnable: script_exec,
    })))
}

/// Result of capturing command output with file descriptors
#[pyclass]
pub struct CapturedResult {
    #[pyo3(get)]
    exit_code: u8,
    stdout_fd: Option<i32>,
    stderr_fd: Option<i32>,
}

#[pymethods]
impl CapturedResult {
    /// Read all stdout, close FD, return as string. Can only call once.
    fn read_stdout(&mut self) -> PyResult<String> {
        let fd = self.stdout_fd.take().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("stdout already consumed")
        })?;

        // Convert raw FD to File (takes ownership)
        let mut file = unsafe { File::from_raw_fd(fd) };
        let mut content = String::new();

        file.read_to_string(&mut content).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to read stdout: {}", e))
        })?;

        // File is automatically closed when dropped
        Ok(content)
    }

    /// Read all stderr, close FD, return as string. Can only call once.
    fn read_stderr(&mut self) -> PyResult<String> {
        let fd = self.stderr_fd.take().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("stderr already consumed")
        })?;

        // Convert raw FD to File (takes ownership)
        let mut file = unsafe { File::from_raw_fd(fd) };
        let mut content = String::new();

        file.read_to_string(&mut content).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(format!("Failed to read stderr: {}", e))
        })?;

        // File is automatically closed when dropped
        Ok(content)
    }

    /// Get raw stdout FD for manual streaming. YOU MUST CLOSE IT!
    #[getter]
    fn stdout_fd(&mut self) -> PyResult<i32> {
        self.stdout_fd.take().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("stdout already consumed")
        })
    }

    /// Get raw stderr FD for manual streaming. YOU MUST CLOSE IT!
    #[getter]
    fn stderr_fd(&mut self) -> PyResult<i32> {
        self.stderr_fd.take().ok_or_else(|| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>("stderr already consumed")
        })
    }

    fn __del__(&mut self) {
        // Safety: close unclosed FDs to prevent FD leaks
        if let Some(fd) = self.stdout_fd {
            unsafe {
                libc::close(fd);
            }
        }
        if let Some(fd) = self.stderr_fd {
            unsafe {
                libc::close(fd);
            }
        }
    }
}

/// Execute a runnable and capture its stdout and stderr
#[pyfunction]
pub fn capture(runnable: &ShipRunnable) -> PyResult<CapturedResult> {
    let result = execute_with_capture(&runnable.into());

    match result {
        ShellResult::Captured {
            exit_code,
            stdout_fd,
            stderr_fd,
        } => Ok(CapturedResult {
            exit_code,
            stdout_fd: Some(stdout_fd),
            stderr_fd: Some(stderr_fd),
        }),
        ShellResult::ExitOnly { .. } => Err(PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
            "Expected captured result but got exit-only result",
        )),
    }
}

/// Convenience function: execute and return just stdout as a string
#[pyfunction]
pub fn get_stdout(runnable: &ShipRunnable) -> PyResult<String> {
    let mut result = capture(runnable)?;
    result.read_stdout()
}

/// Convenience function: execute and return just stderr as a string
#[pyfunction]
pub fn get_stderr(runnable: &ShipRunnable) -> PyResult<String> {
    let mut result = capture(runnable)?;
    result.read_stderr()
}

/// Get an environment variable
#[pyfunction]
pub fn get_env(py: Python, key: String) -> PyResult<Py<PyAny>> {
    match shell::get_var(&key) {
        Some(value) => env_value_to_py(py, &value),
        None => Ok(py.None()),
    }
}

/// Set an environment variable
#[pyfunction]
pub fn set_env(key: String, value: Bound<PyAny>) -> PyResult<()> {
    let env_value = py_to_env_value(&value)?;
    shell::set_var(key, env_value);
    Ok(())
}

/// Mark a variable as exported (will be passed to child processes)
#[pyfunction]
pub fn mark_var_exported(key: String) -> PyResult<()> {
    shell::mark_exported(&key);
    Ok(())
}

/// Check if a variable is exported
#[pyfunction]
pub fn is_var_exported(key: String) -> PyResult<bool> {
    Ok(shell::is_exported(&key))
}

/// Dictionary-like access to environment variables
#[pyclass]
pub struct ShipEnv;

#[pymethods]
impl ShipEnv {
    fn __getitem__(&self, py: Python, key: String) -> PyResult<Py<PyAny>> {
        match shell::get_var(&key) {
            Some(value) => env_value_to_py(py, &value),
            None => Err(PyKeyError::new_err(format!("Key '{}' not found", key))),
        }
    }

    fn __setitem__(&self, key: String, value: Bound<PyAny>) -> PyResult<()> {
        let env_value = py_to_env_value(&value)?;
        shell::set_var(key, env_value);
        Ok(())
    }

    fn __delitem__(&self, key: String) -> PyResult<()> {
        match shell::unset_var(&key) {
            Some(_) => Ok(()),
            None => Err(PyKeyError::new_err(format!("Key '{}' not found", key))),
        }
    }

    fn __contains__(&self, key: String) -> PyResult<bool> {
        Ok(shell::contains_var(&key))
    }

    fn __len__(&self) -> PyResult<usize> {
        Ok(shell::var_count())
    }

    fn keys(&self, py: Python) -> PyResult<Py<PyList>> {
        let keys = shell::all_var_keys();
        Ok(PyList::new(py, &keys)?.into())
    }

    fn values(&self, py: Python) -> PyResult<Py<PyList>> {
        let all_vars = shell::all_vars();
        let values: Result<Vec<Py<PyAny>>, _> =
            all_vars.values().map(|v| env_value_to_py(py, v)).collect();
        Ok(PyList::new(py, &values?)?.into())
    }

    fn items(&self, py: Python) -> PyResult<Py<PyList>> {
        let all_vars = shell::all_vars();
        let items: Result<Vec<(String, Py<PyAny>)>, PyErr> = all_vars
            .iter()
            .map(|(k, v)| Ok((k.clone(), env_value_to_py(py, v)?)))
            .collect();
        Ok(PyList::new(py, &items?)?.into())
    }

    #[pyo3(signature = (key, default=None))]
    fn get(&self, py: Python, key: String, default: Option<Bound<PyAny>>) -> PyResult<Py<PyAny>> {
        match shell::get_var(&key) {
            Some(value) => env_value_to_py(py, &value),
            None => match default {
                Some(d) => Ok(d.unbind()),
                None => Ok(py.None()),
            },
        }
    }
}
