pub mod repl;
pub mod shell;

use anyhow::Result;
use pyo3::prelude::*;
use std::ffi::CString;

// Embed Python modules at compile time
const CORE: &str = include_str!("../../python/shell/core.py");
const SHP_BUILTINS: &str = include_str!("../../python/shell/builtins.py");
const SHP_SHELL_MARKER: &str = include_str!("../../python/shell/shell_marker.py");
const PYTHON_INIT: &str = include_str!("../../python/shell/init.py");

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
    register("core", CORE, None)?;
    register("shp.builtins", SHP_BUILTINS, Some("shp"))?;
    register("shp.shell_marker", SHP_SHELL_MARKER, Some("shp"))?;

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

/// Stage 2: Configure Python environment and register REPL dependencies
/// Call this AFTER shell::initialize_environment()
pub fn configure_repl() -> Result<()> {
    // Register embedded Python modules and run initialization script
    Python::attach(|py| {
        register_embedded_modules(py)?;

        // Initialize Python environment (can now import ship_shell_marker and shp.ergo)
        let init_cstr = CString::new(PYTHON_INIT).unwrap();
        py.run(init_cstr.as_c_str(), None, None)?;
        Ok::<(), PyErr>(())
    })?;

    // Register statement checker with REPL
    crate::repl::set_statement_checker(Box::new(|code: &str| {
        Python::attach(|py| {
            // Import codeop module and get compile_command function
            let result = py
                .import("codeop")
                .and_then(|codeop| codeop.getattr("compile_command"))
                .and_then(|compile_cmd| compile_cmd.call1((code,)));

            match result {
                Ok(obj) if obj.is_none() => false, // None = incomplete
                Ok(_) => true,                     // Code object = complete
                Err(_) => true,                    // Syntax error = let Python report it
            }
        })
    }));

    // Register code executor with REPL
    crate::repl::set_code_executor(Box::new(|code: &str| {
        Python::attach(|py| shell::execute_repl_code(py, code))
    }));

    Ok(())
}

/// The main Python module 'shp'
#[pymodule]
pub mod shp {
    use super::*;

    /// Initialize the module and add the env instance and repl submodule
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
        m.add_function(wrap_pyfunction!(shell::shexec, m)?)?;
        m.add_function(wrap_pyfunction!(shell::capture, m)?)?;
        m.add_function(wrap_pyfunction!(shell::get_stdout, m)?)?;
        m.add_function(wrap_pyfunction!(shell::get_stderr, m)?)?;
        m.add_function(wrap_pyfunction!(shell::get_env, m)?)?;
        m.add_function(wrap_pyfunction!(shell::set_env, m)?)?;

        // Add repl submodule
        let repl_module = PyModule::new(m.py(), "repl")?;
        repl_module.add_function(wrap_pyfunction!(repl::set_prompt, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::get_prompt, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::set_continuation, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::get_continuation, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::set_right_prompt, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::get_right_prompt, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::on, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::off, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::list_hooks, &repl_module)?)?;
        repl_module.add_function(wrap_pyfunction!(repl::print_hooks, &repl_module)?)?;
        repl_module.add_class::<repl::REPLHook>()?;
        m.add_submodule(&repl_module)?;

        Ok(())
    }
}
