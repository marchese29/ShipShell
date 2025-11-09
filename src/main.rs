mod bindings;
mod shell;

use anyhow::Result;
use bindings::shp;
use pyo3::prelude::*;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;
use std::ffi::CString;

// Embed Python modules at compile time
const PYTHON_INIT: &str = include_str!("../python/init.py");
const SHIP_SHELL_MARKER: &str = include_str!("../python/ship_shell_marker.py");
const SHIP_ERGO_BUILTINS: &str = include_str!("../python/ship_ergo/builtins.py");
const SHIP_ERGO_SHELL: &str = include_str!("../python/ship_ergo/shell.py");
const SHIP_ERGO_INIT: &str = include_str!("../python/ship_ergo/__init__.py");

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

    // Register all embedded modules (register submodules before the package)
    register("ship_shell_marker", SHIP_SHELL_MARKER, None)?;
    register("ship_ergo.builtins", SHIP_ERGO_BUILTINS, Some("ship_ergo"))?;
    register("ship_ergo.shell", SHIP_ERGO_SHELL, Some("ship_ergo"))?;
    register("ship_ergo", SHIP_ERGO_INIT, Some("ship_ergo"))?;

    Ok(())
}

fn main() -> Result<()> {
    // Register the shp module before initializing Python
    pyo3::append_to_inittab!(shp);

    // Initialize Python interpreter
    Python::initialize();

    // Initialize shell environment from parent process
    shell::init_from_parent();

    // Create rustyline editor for REPL
    let mut rl = DefaultEditor::new()?;

    println!("ShipShell Python REPL");
    println!("Type 'exit()' or press Ctrl+D to quit");
    println!();

    // Register embedded Python modules
    Python::attach(|py| {
        register_embedded_modules(py)?;
        Ok::<(), PyErr>(())
    })?;

    // Initialize Python environment (can now import ship_shell_marker and ship_ergo)
    Python::attach(|py| {
        let init_cstr = CString::new(PYTHON_INIT).unwrap();
        py.run(init_cstr.as_c_str(), None, None)?;
        Ok::<(), PyErr>(())
    })?;

    // Main REPL loop
    loop {
        // Read input with "ship>" prompt
        let readline = rl.readline("ship> ");

        match readline {
            Ok(line) => {
                // Add to history
                let _ = rl.add_history_entry(line.as_str());

                // Skip empty lines
                if line.trim().is_empty() {
                    continue;
                }

                // Execute Python code with auto-run for ShipRunnable
                Python::attach(|py| {
                    if let Err(e) = bindings::execute_repl_line(py, &line) {
                        e.print(py);
                    }
                });
            }
            Err(ReadlineError::Interrupted) => {
                // Ctrl+C - continue REPL
                println!("^C");
                continue;
            }
            Err(ReadlineError::Eof) => {
                // Ctrl+D - exit REPL
                println!("Exiting...");
                break;
            }
            Err(err) => {
                println!("Error: {:?}", err);
                break;
            }
        }
    }

    Ok(())
}
