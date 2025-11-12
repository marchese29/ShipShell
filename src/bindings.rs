use nix::libc;
use pyo3::exceptions::PyKeyError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;
use std::ffi::CString;
use std::sync::Arc;

use crate::shell::{self, EnvValue, ExecRequest, execute};

/// Execute a line of Python code in REPL mode with auto-run for ShipRunnable
pub fn execute_repl_line(py: Python, line: &str) -> PyResult<()> {
    let code = CString::new(line)?;

    // Try to evaluate as an expression first
    let eval_result = py.eval(code.as_c_str(), None, None);

    match eval_result {
        Ok(result) => {
            // Check if it's a ShipRunnable - auto-run it
            if result.is_instance_of::<shp::ShipRunnable>() {
                // Call the Python __call__ method (i.e., invoke the runnable)
                let exec_result = result.call0()?;
                // Check if exit code is non-zero
                if let Ok(ship_result) = exec_result.extract::<shp::ShipResult>()
                    && ship_result.exit_code != 0
                {
                    println!("Exit code: {}", ship_result.exit_code);
                }
            } else if !result.is_none() {
                // Print the result
                println!("{}", result.repr()?);
            }

            Ok(())
        }
        Err(_) => {
            // If eval fails, try running as a statement
            py.run(code.as_c_str(), None, None)
        }
    }
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

#[pymodule]
pub mod shp {
    use super::*;

    /// Initialize the module and add the env instance
    #[pymodule_init]
    fn init(m: &Bound<PyModule>) -> PyResult<()> {
        m.add("env", Py::new(m.py(), ShipEnv)?)?;
        Ok(())
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
                    let mut stages: Vec<ExecRequest> =
                        predecessors.iter().map(|p| p.into()).collect();
                    stages.push(final_cmd.into());
                    ExecRequest::Pipeline { stages }
                }
                Runnable::Subshell { runnable } => ExecRequest::Subshell {
                    request: Box::new(runnable.into()),
                },
                Runnable::Redirect { runnable, target } => {
                    let shell_target = match target {
                        RedirectTarget::FilePath { path, append } => {
                            shell::RedirectTarget::FilePath {
                                path: path.clone(),
                                append: *append,
                            }
                        }
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
                // (Command, Subshell, and WithEnv are all atomic units)
                (
                    Command { .. } | Subshell { .. } | WithEnv { .. },
                    Command { .. } | Subshell { .. } | WithEnv { .. },
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
                    Command { .. } | Subshell { .. } | WithEnv { .. },
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
                    Command { .. } | Subshell { .. } | WithEnv { .. },
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
                exit_code: result.exit_code,
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
                PyErr::new::<pyo3::exceptions::PyTypeError, _>(
                    "with_env() requires keyword arguments",
                )
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
    }

    #[pyfunction]
    #[pyo3(signature = (name))]
    fn prog(name: String) -> PyResult<ShipProgram> {
        // TODO: Resolve the program from the shell environment
        Ok(ShipProgram { name })
    }

    #[pyfunction]
    #[pyo3(signature = (prog, *args))]
    fn cmd(prog: ShipProgram, args: Vec<String>) -> PyResult<ShipRunnable> {
        // PyO3 automatically converts:
        // - cmd to String (calls __str__ if needed)
        // - each arg to String (calls __str__ if needed)
        Ok(ShipRunnable(Arc::new(Runnable::Command { prog, args })))
    }

    #[pyfunction]
    #[pyo3(signature = (cmd1, cmd2, *cmds))]
    fn pipe(
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
    fn sub(runnable: ShipRunnable) -> PyResult<ShipRunnable> {
        Ok(ShipRunnable(Arc::new(Runnable::Subshell { runnable })))
    }

    #[pyfunction]
    fn shexec(runnable: &ShipRunnable) -> PyResult<ShipResult> {
        runnable.__call__()
    }

    /// Get an environment variable
    #[pyfunction]
    fn get_env(py: Python, key: String) -> PyResult<Py<PyAny>> {
        match shell::get_var(&key) {
            Some(value) => env_value_to_py(py, &value),
            None => Ok(py.None()),
        }
    }

    /// Set an environment variable
    #[pyfunction]
    fn set_env(key: String, value: Bound<PyAny>) -> PyResult<()> {
        let env_value = py_to_env_value(&value)?;
        shell::set_var(key, env_value);
        Ok(())
    }

    /// Dictionary-like access to environment variables
    #[pyclass]
    struct ShipEnv;

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
        fn get(
            &self,
            py: Python,
            key: String,
            default: Option<Bound<PyAny>>,
        ) -> PyResult<Py<PyAny>> {
            match shell::get_var(&key) {
                Some(value) => env_value_to_py(py, &value),
                None => match default {
                    Some(d) => Ok(d.unbind()),
                    None => Ok(py.None()),
                },
            }
        }
    }
}
