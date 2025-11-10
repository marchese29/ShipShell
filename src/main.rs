mod bindings;
mod shell;

use anyhow::Result;
use bindings::shp;
use pyo3::prelude::*;
use rustyline::error::ReadlineError;
use rustyline::{Config, Editor};
use std::ffi::CString;

// Embed Python modules at compile time
const CORE: &str = include_str!("../python/core.py");
const PYTHON_INIT: &str = include_str!("../python/init.py");
const SHIP_SHELL_MARKER: &str = include_str!("../python/ship_shell_marker.py");
const SHP_ERGO_BUILTINS: &str = include_str!("../python/shp/ergo/builtins.py");
const SHP_ERGO_INIT: &str = include_str!("../python/shp/ergo/__init__.py");

/// Check if a Python statement is complete using Python's codeop module
fn is_complete_python_statement(code: &str) -> bool {
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
}

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
    register("ship_shell_marker", SHIP_SHELL_MARKER, None)?;
    register("shp.ergo.builtins", SHP_ERGO_BUILTINS, Some("shp.ergo"))?;
    register("shp.ergo", SHP_ERGO_INIT, Some("shp.ergo"))?;

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
    let config = Config::builder().build();
    let mut rl: Editor<(), _> = Editor::with_config(config)?;

    println!("ShipShell Python REPL");
    println!("Type 'exit()' or press Ctrl+D to quit");
    println!();

    // Register embedded Python modules
    Python::attach(|py| {
        register_embedded_modules(py)?;
        Ok::<(), PyErr>(())
    })?;

    // Initialize Python environment (can now import ship_shell_marker and shp.ergo)
    Python::attach(|py| {
        let init_cstr = CString::new(PYTHON_INIT).unwrap();
        py.run(init_cstr.as_c_str(), None, None)?;
        Ok::<(), PyErr>(())
    })?;

    // Main REPL loop
    let mut buffer = String::new();
    loop {
        // Use different prompt for continuation lines
        let prompt = if buffer.is_empty() {
            "ship> "
        } else {
            "..... "
        };

        let readline = rl.readline(prompt);

        match readline {
            Ok(line) => {
                // Append line to buffer
                if !buffer.is_empty() {
                    buffer.push('\n');
                }
                buffer.push_str(&line);

                // Check if statement is complete
                if is_complete_python_statement(&buffer) {
                    // Add complete statement to history
                    let _ = rl.add_history_entry(buffer.as_str());

                    // Skip empty statements
                    if !buffer.trim().is_empty() {
                        // Execute Python code with auto-run for ShipRunnable
                        Python::attach(|py| {
                            if let Err(e) = bindings::execute_repl_line(py, &buffer) {
                                e.print(py);
                            }
                        });
                    }

                    // Clear buffer for next statement
                    buffer.clear();
                }
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
