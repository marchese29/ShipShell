pub mod shell;

use anyhow::Result;
use pyo3::prelude::*;
use std::ffi::CString;

// Embed Python modules at compile time
const CORE: &str = include_str!("../../python/shell/core.py");
const SHP_BUILTINS: &str = include_str!("../../python/shell/builtins.py");
const SHP_SHELL_MARKER: &str = include_str!("../../python/shell/shell_marker.py");
const SHP_PY_ENV: &str = include_str!("../../python/shell/py_env.py");
const PYTHON_INIT: &str = include_str!("../../python/shell/init.py");
const REPL: &str = include_str!("../../python/shell/repl.py");
const REPL_INTERNAL: &str = include_str!("../../python/shell/_repl_internal.py");

/// Register embedded Python modules in sys.modules
fn register_embedded_modules(py: Python) -> PyResult<()> {
    let sys_modules = py.import("sys")?.getattr("modules")?;

    // Helper closure to register a module
    let register = |name: &str, code: &str, package: Option<&str>| -> PyResult<()> {
        let module = PyModule::new(py, name)?;

        // Set __package__ for proper relative imports
        if let Some(pkg) = package {
            module.setattr("__package__", pkg)?;
        }

        let code_cstr = CString::new(code).unwrap();
        py.run(code_cstr.as_c_str(), Some(&module.dict()), None)?;
        sys_modules.set_item(name, module)?;
        Ok(())
    };

    // Register all embedded modules
    // Note: We DON'T register the Python shp stub - the Rust native module is already registered
    // The shp/__init__.py file is only for external IDE/script support
    // Register shp.* modules first since repl depends on shp.py_env
    register("core", CORE, None)?;
    register("shp.builtins", SHP_BUILTINS, Some("shp"))?;
    register("shp.py_env", SHP_PY_ENV, Some("shp"))?;
    register("shp.shell_marker", SHP_SHELL_MARKER, Some("shp"))?;
    register("repl", REPL, None)?;

    Ok(())
}

/// Stage 1: Initialize Python runtime (bare interpreter)
/// Call this BEFORE shell::initialize_environment()
pub fn initialize_runtime() -> Result<()> {
    // Register the shp module before initializing Python
    pyo3::append_to_inittab!(shp);

    // Initialize Python interpreter
    Python::initialize();

    Ok(())
}

/// Stage 2: Configure Python environment
/// Call this AFTER shell::initialize_environment()
pub fn configure_python_env() -> Result<()> {
    // Register embedded Python modules and run initialization script
    Python::attach(|py| {
        register_embedded_modules(py)?;

        // Initialize Python environment (can now import ship_shell_marker and shp.ergo)
        let init_cstr = CString::new(PYTHON_INIT).unwrap();
        py.run(init_cstr.as_c_str(), None, None)?;

        // Initialize the REPL
        let repl_internal_module = PyModule::new(py, "_repl_internal")?;
        let repl_cstr = CString::new(REPL_INTERNAL).unwrap();
        py.run(
            repl_cstr.as_c_str(),
            Some(&repl_internal_module.dict()),
            None,
        )?;

        // Register the internal REPL module
        let sys_modules = py.import("sys")?.getattr("modules")?;
        sys_modules.set_item("_repl_internal", repl_internal_module)?;
        Ok::<(), PyErr>(())
    })?;

    // Register code executor with shell
    crate::shell::set_code_executor(Box::new(|code: &str| shell::execute_python_code(code)));

    Ok(())
}

/// Run the Python-based REPL
pub fn run_python_repl() -> Result<()> {
    Python::attach(|py| {
        let repl = py.import("repl")?;
        repl.getattr("run_repl")?.call0()?;
        Ok::<(), PyErr>(())
    })?;
    Ok(())
}

/// The main Python module 'shp'
#[pymodule]
pub mod shp {
    use super::*;

    /// Initialize the module and add the env instance
    #[pymodule_init]
    fn init(m: &Bound<PyModule>) -> PyResult<()> {
        // Add environment singleton
        m.add("env", Py::new(m.py(), shell::ShipEnv)?)?;

        // Add shell classes
        m.add_class::<shell::ShipProgram>()?;
        m.add_class::<shell::ShipRunnable>()?;
        m.add_class::<shell::ShipResult>()?;
        m.add_class::<shell::CapturedResult>()?;
        m.add_class::<shell::ShipEnv>()?;

        // Add shell functions
        m.add_function(wrap_pyfunction!(shell::prog, m)?)?;
        m.add_function(wrap_pyfunction!(shell::cmd, m)?)?;
        m.add_function(wrap_pyfunction!(shell::pipe, m)?)?;
        m.add_function(wrap_pyfunction!(shell::sub, m)?)?;
        m.add_function(wrap_pyfunction!(shell::shpexec, m)?)?;
        m.add_function(wrap_pyfunction!(shell::capture, m)?)?;
        m.add_function(wrap_pyfunction!(shell::get_stdout, m)?)?;
        m.add_function(wrap_pyfunction!(shell::get_stderr, m)?)?;
        m.add_function(wrap_pyfunction!(shell::get_env, m)?)?;
        m.add_function(wrap_pyfunction!(shell::set_env, m)?)?;

        Ok(())
    }
}
