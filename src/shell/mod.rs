pub mod env;
pub mod exec;

// Re-export commonly used types and functions
pub use env::{
    EnvValue, all_var_keys, all_vars, contains_var, get_var, init_from_parent, set_var, unset_var,
    var_count,
};
pub use exec::{CommandSpec, RedirectTarget, execute};
