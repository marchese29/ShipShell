pub mod builtins;
pub mod env;
pub mod exec;

use std::sync::OnceLock;

// Re-export commonly used types and functions
pub use env::{
    EnvValue, all_var_keys, all_vars, contains_var, get_var, initialize_environment, is_exported,
    mark_exported, set_last_exit, set_var, unset_var, var_count,
};
pub use exec::{ExecRequest, RedirectTarget, execute};

/// Code executor function type
type CodeExecutor = Box<dyn Fn(&str) -> anyhow::Result<()> + Send + Sync>;
static CODE_EXECUTOR: OnceLock<CodeExecutor> = OnceLock::new();

/// Set the code executor (called during initialization)
pub fn set_code_executor(executor: CodeExecutor) {
    CODE_EXECUTOR.set(executor).ok();
}

/// Get the code code executor
pub fn get_code_executor() -> Option<&'static CodeExecutor> {
    CODE_EXECUTOR.get()
}
