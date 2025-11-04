mod bindings;
mod shell_env;

use anyhow::Result;
use bindings::shp;
use pyo3::prelude::*;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;
use std::ffi::CString;

// Embed Python initialization files at compile time
const PYTHON_INIT: &str = include_str!("../python/init.py");

fn main() -> Result<()> {
    // Register the shp module before initializing Python
    pyo3::append_to_inittab!(shp);

    // Initialize Python interpreter
    Python::initialize();

    // Initialize shell environment from parent process
    shell_env::init_from_parent();

    // Create rustyline editor for REPL
    let mut rl = DefaultEditor::new()?;

    println!("ShipShell Python REPL");
    println!("Type 'exit()' or press Ctrl+D to quit");
    println!();

    // Initialize Python environment
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
