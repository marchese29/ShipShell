use pyo3::prelude::*;

/// REPL hook enum - exposed to Python
#[pyclass(eq, eq_int)]
#[derive(Clone, Copy, PartialEq, Eq, Hash)]
pub enum REPLHook {
    BeforePrompt,
    BeforeContinuation,
    BeforeExecute,
    AfterExecute,
}

/// Set the primary prompt string
#[pyfunction]
pub fn set_prompt(value: String) -> PyResult<()> {
    crate::repl::set_primary_prompt(value);
    Ok(())
}

/// Get the current primary prompt string
#[pyfunction]
pub fn get_prompt() -> PyResult<String> {
    Ok(crate::repl::get_primary_prompt())
}

/// Set the continuation prompt string
#[pyfunction]
pub fn set_continuation(value: String) -> PyResult<()> {
    crate::repl::set_continuation_prompt(value);
    Ok(())
}

/// Get the current continuation prompt string
#[pyfunction]
pub fn get_continuation() -> PyResult<String> {
    Ok(crate::repl::get_continuation_prompt())
}

/// Set the right prompt string
#[pyfunction]
pub fn set_right_prompt(value: String) -> PyResult<()> {
    crate::repl::set_right_prompt(value);
    Ok(())
}

/// Get the current right prompt string
#[pyfunction]
pub fn get_right_prompt() -> PyResult<String> {
    Ok(crate::repl::get_right_prompt())
}

/// Register a callback for a REPL hook
/// Wraps Python callable in Rust closure and registers with REPL
/// Returns a unique ID for this hook registration
#[pyfunction]
pub fn on(hook: REPLHook, callback: Py<PyAny>) -> PyResult<u64> {
    let id = match hook {
        REPLHook::BeforePrompt => {
            let rust_hook = Box::new(move || {
                Python::attach(|py| {
                    if let Err(e) = callback.call0(py) {
                        eprintln!("Error in REPL hook handler:");
                        e.print(py);
                    }
                });
            });
            crate::repl::register_before_prompt_hook(rust_hook)
        }
        REPLHook::BeforeContinuation => {
            let rust_hook = Box::new(move |prev_prompt: &str, buffer: &str| {
                Python::attach(|py| {
                    if let Err(e) = callback.call1(py, (prev_prompt, buffer)) {
                        eprintln!("Error in REPL hook handler:");
                        e.print(py);
                    }
                });
            });
            crate::repl::register_before_continuation_hook(rust_hook)
        }
        REPLHook::BeforeExecute => {
            let rust_hook = Box::new(move |command: &str| {
                Python::attach(|py| {
                    if let Err(e) = callback.call1(py, (command,)) {
                        eprintln!("Error in REPL hook handler:");
                        e.print(py);
                    }
                });
            });
            crate::repl::register_before_execute_hook(rust_hook)
        }
        REPLHook::AfterExecute => {
            let rust_hook = Box::new(move |command: &str| {
                Python::attach(|py| {
                    if let Err(e) = callback.call1(py, (command,)) {
                        eprintln!("Error in REPL hook handler:");
                        e.print(py);
                    }
                });
            });
            crate::repl::register_after_execute_hook(rust_hook)
        }
    };
    Ok(id)
}

/// Unregister a callback from a REPL hook by ID
/// Returns True if the hook was found and removed, False otherwise
#[pyfunction]
pub fn off(hook: REPLHook, id: u64) -> PyResult<bool> {
    let removed = match hook {
        REPLHook::BeforePrompt => crate::repl::unregister_before_prompt_hook(id),
        REPLHook::BeforeContinuation => crate::repl::unregister_before_continuation_hook(id),
        REPLHook::BeforeExecute => crate::repl::unregister_before_execute_hook(id),
        REPLHook::AfterExecute => crate::repl::unregister_after_execute_hook(id),
    };
    Ok(removed)
}

/// List all registered hook IDs for a specific hook type
#[pyfunction]
pub fn list_hooks(hook: REPLHook) -> PyResult<Vec<u64>> {
    let ids = match hook {
        REPLHook::BeforePrompt => crate::repl::list_before_prompt_hook_ids(),
        REPLHook::BeforeContinuation => crate::repl::list_before_continuation_hook_ids(),
        REPLHook::BeforeExecute => crate::repl::list_before_execute_hook_ids(),
        REPLHook::AfterExecute => crate::repl::list_after_execute_hook_ids(),
    };
    Ok(ids)
}

/// Print all registered hooks grouped by type
/// Displays each hook type with its registered IDs in order
#[pyfunction]
pub fn print_hooks() -> PyResult<()> {
    println!("Registered REPL Hooks:");
    println!(
        "  BeforePrompt: {:?}",
        crate::repl::list_before_prompt_hook_ids()
    );
    println!(
        "  BeforeContinuation: {:?}",
        crate::repl::list_before_continuation_hook_ids()
    );
    println!(
        "  BeforeExecute: {:?}",
        crate::repl::list_before_execute_hook_ids()
    );
    println!(
        "  AfterExecute: {:?}",
        crate::repl::list_after_execute_hook_ids()
    );
    Ok(())
}
