pub mod builtins;
pub mod env;
pub mod exec;

// Re-export commonly used types and functions
pub use env::{
    EnvValue, all_var_keys, all_vars, contains_var, get_var, initialize, set_last_exit, set_var,
    unset_var, var_count,
};
pub use exec::{ExecRequest, RedirectTarget, execute};
