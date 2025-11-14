mod py_bindings;
mod repl;
mod shell;

use anyhow::Result;

fn main() -> Result<()> {
    // Stage 1: Initialize Python runtime (bare interpreter)
    py_bindings::initialize_runtime()?;

    // Initialize shell environment from parent process
    shell::initialize_environment();

    // Stage 2: Configure Python environment and register REPL dependencies
    py_bindings::configure_repl()?;

    // Run the REPL
    repl::run()
}
