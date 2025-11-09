use std::collections::HashMap;
use std::ffi::CString;
use std::path::PathBuf;
use std::sync::{OnceLock, RwLock};

/// Represents a value that can be stored in the shell environment
#[derive(Debug, Clone, PartialEq)]
pub enum EnvValue {
    String(String),
    Integer(i64),
    Decimal(f64),
    Bool(bool),
    None,
    List(Vec<EnvValue>),
    FilePath(PathBuf),
}

impl EnvValue {
    /// Recursively convert an EnvValue to a string representation
    /// Used for converting environment variables to strings for child processes
    pub(crate) fn to_string_repr(&self) -> String {
        match self {
            EnvValue::String(s) => s.clone(),
            EnvValue::Integer(i) => i.to_string(),
            EnvValue::Decimal(d) => d.to_string(),
            EnvValue::Bool(b) => {
                if *b {
                    "True".to_string()
                } else {
                    "False".to_string()
                }
            }
            EnvValue::None => String::new(), // Empty string
            EnvValue::List(items) => items
                .iter()
                .map(|item| item.to_string_repr()) // Recursive!
                .collect::<Vec<_>>()
                .join(":"),
            EnvValue::FilePath(path) => path.to_string_lossy().to_string(),
        }
    }

    /// Parse a string value into an EnvValue, attempting to detect the appropriate type
    /// Priority order ensures roundtrip consistency and proper handling of edge cases
    fn parse_from_string(s: &str) -> EnvValue {
        // 1. Empty string → None
        if s.is_empty() {
            return EnvValue::None;
        }

        // 2. Exact "True" → Bool(true)
        if s == "True" {
            return EnvValue::Bool(true);
        }

        // 3. Exact "False" → Bool(false)
        if s == "False" {
            return EnvValue::Bool(false);
        }

        // 4. Valid integer (without decimal point)
        if !s.contains('.')
            && let Ok(i) = s.parse::<i64>()
        {
            return EnvValue::Integer(i);
        }

        // 5. Valid decimal (with decimal point)
        if s.contains('.')
            && let Ok(f) = s.parse::<f64>()
        {
            return EnvValue::Decimal(f);
        }

        // 6. Contains ":" → List (recursively parsed, BEFORE path check)
        // This prevents paths with colons (like PATH=/usr/bin:/bin) from being incorrectly split
        if s.contains(':') {
            let items: Vec<EnvValue> = s.split(':').map(EnvValue::parse_from_string).collect();
            return EnvValue::List(items);
        }

        // 7. Path-like patterns → FilePath (stored unresolved)
        // Check for common path patterns (no filesystem check needed)
        if s.starts_with('/') ||                    // Absolute Unix path: /usr/bin
           (s.starts_with('~') && s.contains('/')) || // Home-relative path: ~/Documents
           s.starts_with("./") ||                    // Current directory: ./file.txt
           s.starts_with("../")
        {
            // Parent directory: ../file.txt
            return EnvValue::FilePath(PathBuf::from(s));
        }

        // 8. Everything else → String
        EnvValue::String(s.to_string())
    }
}

/// The shell's environment, containing all environment variables
pub struct ShellEnvironment {
    env_vars: HashMap<String, EnvValue>,
}

impl ShellEnvironment {
    /// Create a new empty shell environment
    pub fn new() -> Self {
        Self {
            env_vars: HashMap::new(),
        }
    }

    /// Create a new shell environment initialized from the parent process
    pub fn from_parent() -> Self {
        let mut env_vars = HashMap::new();
        for (key, value) in std::env::vars() {
            env_vars.insert(key, EnvValue::parse_from_string(&value));
        }
        Self { env_vars }
    }

    /// Get an environment variable value
    pub fn get(&self, key: &str) -> Option<&EnvValue> {
        self.env_vars.get(key)
    }

    /// Set an environment variable
    pub fn set(&mut self, key: String, value: EnvValue) {
        self.env_vars.insert(key, value);
    }

    /// Remove an environment variable
    pub fn unset(&mut self, key: &str) -> Option<EnvValue> {
        self.env_vars.remove(key)
    }

    /// Get all environment variables
    pub fn all_vars(&self) -> &HashMap<String, EnvValue> {
        &self.env_vars
    }

    /// Get all environment variable keys
    pub fn keys(&self) -> impl Iterator<Item = &String> {
        self.env_vars.keys()
    }

    /// Check if a key exists
    pub fn contains_key(&self, key: &str) -> bool {
        self.env_vars.contains_key(key)
    }

    /// Get the number of environment variables
    pub fn len(&self) -> usize {
        self.env_vars.len()
    }

    /// Convert environment to Vec<CString> in "KEY=VALUE" format for execve
    pub fn to_envp(&self) -> Vec<CString> {
        self.env_vars
            .iter()
            .filter_map(|(key, value)| {
                let value_str = value.to_string_repr();
                // Include all variables, even those with empty string values (EnvValue::None)
                CString::new(format!("{}={}", key, value_str)).ok()
            })
            .collect()
    }
}

/// Global shell environment instance
static SHELL_ENV: OnceLock<RwLock<ShellEnvironment>> = OnceLock::new();

/// Get a reference to the global shell environment
pub(crate) fn get_shell_env() -> &'static RwLock<ShellEnvironment> {
    SHELL_ENV.get_or_init(|| RwLock::new(ShellEnvironment::new()))
}

/// Get an environment variable value
pub fn get_var(key: &str) -> Option<EnvValue> {
    let env = get_shell_env();
    let env_read = env.read().unwrap();
    env_read.get(key).cloned()
}

/// Set an environment variable
pub fn set_var(key: String, value: EnvValue) {
    let env = get_shell_env();
    let mut env_write = env.write().unwrap();
    env_write.set(key, value);
}

/// Remove an environment variable
pub fn unset_var(key: &str) -> Option<EnvValue> {
    let env = get_shell_env();
    let mut env_write = env.write().unwrap();
    env_write.unset(key)
}

/// Check if an environment variable exists
pub fn contains_var(key: &str) -> bool {
    let env = get_shell_env();
    let env_read = env.read().unwrap();
    env_read.contains_key(key)
}

/// Get the number of environment variables
pub fn var_count() -> usize {
    let env = get_shell_env();
    let env_read = env.read().unwrap();
    env_read.len()
}

/// Get all environment variable keys
pub fn all_var_keys() -> Vec<String> {
    let env = get_shell_env();
    let env_read = env.read().unwrap();
    env_read.keys().cloned().collect()
}

/// Get all environment variables as a HashMap
pub fn all_vars() -> HashMap<String, EnvValue> {
    let env = get_shell_env();
    let env_read = env.read().unwrap();
    env_read.all_vars().clone()
}

/// Initialize the shell environment from the parent process
pub fn init_from_parent() {
    let env = get_shell_env();
    let mut env_write = env.write().unwrap();
    *env_write = ShellEnvironment::from_parent();
}
