mod bindings;
mod shell;

use anyhow::Result;
use bindings::shp;
use pyo3::prelude::*;
use reedline::{
    Prompt, PromptEditMode, PromptHistorySearch, PromptHistorySearchStatus, Reedline, Signal,
};
use std::borrow::Cow;
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

/// Custom prompt for ShipShell with support for continuation lines and right prompt
struct ShipPrompt {
    is_continuation: bool,
    right_prompt: Option<String>,
}

impl ShipPrompt {
    fn new() -> Self {
        Self {
            is_continuation: false,
            right_prompt: None,
        }
    }
}

impl Prompt for ShipPrompt {
    fn render_prompt_left(&self) -> Cow<'_, str> {
        if self.is_continuation {
            Cow::Borrowed("..... ")
        } else {
            Cow::Borrowed("ship> ")
        }
    }

    fn render_prompt_right(&self) -> Cow<'_, str> {
        self.right_prompt
            .as_deref()
            .map(Cow::Borrowed)
            .unwrap_or(Cow::Borrowed(""))
    }

    fn render_prompt_indicator(&self, _mode: PromptEditMode) -> Cow<'_, str> {
        Cow::Borrowed("")
    }

    fn render_prompt_multiline_indicator(&self) -> Cow<'_, str> {
        Cow::Borrowed("")
    }

    fn render_prompt_history_search_indicator(
        &self,
        history_search: PromptHistorySearch,
    ) -> Cow<'_, str> {
        let prefix = match history_search.status {
            PromptHistorySearchStatus::Passing => "",
            PromptHistorySearchStatus::Failing => "failing ",
        };
        Cow::Owned(format!("({}reverse search) ", prefix))
    }
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
    shell::initialize_environment();

    // Create reedline editor with in-memory history (default)
    let mut line_editor = Reedline::create();

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
    let mut prompt = ShipPrompt::new();

    loop {
        // Update prompt state based on whether we're in continuation mode
        prompt.is_continuation = !buffer.is_empty();

        // Example: Set a right prompt (can be customized later)
        // prompt.right_prompt = Some(format!("🐍 Python"));

        let sig = line_editor.read_line(&prompt);

        match sig {
            Ok(Signal::Success(line)) => {
                // Append line to buffer
                if !buffer.is_empty() {
                    buffer.push('\n');
                }
                buffer.push_str(&line);

                // Check if statement is complete
                if is_complete_python_statement(&buffer) {
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
            Ok(Signal::CtrlC) => {
                // Ctrl+C - clear buffer and continue REPL
                println!("^C");
                buffer.clear();
                continue;
            }
            Ok(Signal::CtrlD) => {
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
