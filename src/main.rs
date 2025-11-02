mod bindings;

use anyhow::Result;
use bindings::shp;
use pyo3::prelude::*;
use rustyline::DefaultEditor;
use rustyline::error::ReadlineError;
use std::ffi::CString;

fn main() -> Result<()> {
    // Register the shp module before initializing Python
    pyo3::append_to_inittab!(shp);

    // Initialize Python interpreter
    Python::initialize();

    // Create rustyline editor for REPL
    let mut rl = DefaultEditor::new()?;

    println!("ShipShell Python REPL");
    println!("Type 'exit()' or press Ctrl+D to quit");
    println!();

    // Import shp module into __main__ namespace
    Python::attach(|py| py.run(c"from shp import *", None, None))?;

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

                // Execute Python code with attach
                Python::attach(|py| {
                    // Convert to CString for PyO3
                    if let Ok(code) = CString::new(line.as_str()) {
                        // Try to evaluate as an expression first
                        match py.eval(code.as_c_str(), None, None) {
                            Ok(result) => {
                                // Check if result is not None
                                if !result.is_none() {
                                    // Print the result
                                    println!("{}", result);
                                }
                            }
                            Err(_) => {
                                // If eval fails, try running as a statement
                                if let Err(e) = py.run(code.as_c_str(), None, None) {
                                    // Print Python errors
                                    e.print(py);
                                }
                            }
                        }
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
