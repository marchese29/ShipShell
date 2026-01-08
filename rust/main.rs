mod py_bindings;
mod shell;

use anyhow::Result;

fn main() -> Result<()> {
    // Stage 1: Initialize Python runtime (bare interpreter)
    py_bindings::initialize_runtime()?;

    // Initialize shell environment from parent process
    shell::initialize_environment();

    // Stage 2: Configure Python environment
    py_bindings::configure_python_env()?;

    // Run the Python-based REPL
    py_bindings::run_python_repl()
}
