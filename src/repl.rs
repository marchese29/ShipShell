use reedline::{
    Prompt, PromptEditMode, PromptHistorySearch, PromptHistorySearchStatus, Reedline, Signal,
};
use std::borrow::Cow;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{OnceLock, RwLock};

/// REPL state storage
pub struct REPLState {
    pub primary_prompt: String,
    pub continuation_prompt: String,
    pub right_prompt: String,
}

/// Global REPL state instance
static REPL_STATE: OnceLock<RwLock<REPLState>> = OnceLock::new();

/// Get a reference to the global REPL state
fn get_repl_state() -> &'static RwLock<REPLState> {
    REPL_STATE.get_or_init(|| {
        RwLock::new(REPLState {
            primary_prompt: "ship> ".to_string(),
            continuation_prompt: "..... ".to_string(),
            right_prompt: String::new(),
        })
    })
}

/// Set the primary prompt string
pub fn set_primary_prompt(value: String) {
    let state = get_repl_state();
    let mut state_write = state.write().unwrap();
    state_write.primary_prompt = value;
}

/// Get the current primary prompt string
pub fn get_primary_prompt() -> String {
    let state = get_repl_state();
    let state_read = state.read().unwrap();
    state_read.primary_prompt.clone()
}

/// Set the continuation prompt string
pub fn set_continuation_prompt(value: String) {
    let state = get_repl_state();
    let mut state_write = state.write().unwrap();
    state_write.continuation_prompt = value;
}

/// Get the current continuation prompt string
pub fn get_continuation_prompt() -> String {
    let state = get_repl_state();
    let state_read = state.read().unwrap();
    state_read.continuation_prompt.clone()
}

/// Set the right prompt string
pub fn set_right_prompt(value: String) {
    let state = get_repl_state();
    let mut state_write = state.write().unwrap();
    state_write.right_prompt = value;
}

/// Get the current right prompt string
pub fn get_right_prompt() -> String {
    let state = get_repl_state();
    let state_read = state.read().unwrap();
    state_read.right_prompt.clone()
}

/// Hook types
pub type BeforePromptHook = Box<dyn Fn() + Send + Sync>;
pub type BeforeContinuationHook = Box<dyn Fn(&str, &str) + Send + Sync>;
pub type BeforeExecuteHook = Box<dyn Fn(&str) + Send + Sync>;
pub type AfterExecuteHook = Box<dyn Fn(&str) + Send + Sync>;

/// Atomic counters for hook IDs (separate ID space per hook type)
static BEFORE_PROMPT_COUNTER: AtomicU64 = AtomicU64::new(1);
static BEFORE_CONTINUATION_COUNTER: AtomicU64 = AtomicU64::new(1);
static BEFORE_EXECUTE_COUNTER: AtomicU64 = AtomicU64::new(1);
static AFTER_EXECUTE_COUNTER: AtomicU64 = AtomicU64::new(1);

/// Hook storage with IDs (Vec maintains registration order)
struct Hooks {
    before_prompt: Vec<(u64, BeforePromptHook)>,
    before_continuation: Vec<(u64, BeforeContinuationHook)>,
    before_execute: Vec<(u64, BeforeExecuteHook)>,
    after_execute: Vec<(u64, AfterExecuteHook)>,
}

static HOOKS: OnceLock<RwLock<Hooks>> = OnceLock::new();

fn get_hooks() -> &'static RwLock<Hooks> {
    HOOKS.get_or_init(|| {
        RwLock::new(Hooks {
            before_prompt: Vec::new(),
            before_continuation: Vec::new(),
            before_execute: Vec::new(),
            after_execute: Vec::new(),
        })
    })
}

/// Register hooks - returns unique ID for the hook
pub fn register_before_prompt_hook(hook: BeforePromptHook) -> u64 {
    let id = BEFORE_PROMPT_COUNTER.fetch_add(1, Ordering::SeqCst);
    get_hooks().write().unwrap().before_prompt.push((id, hook));
    id
}

pub fn register_before_continuation_hook(hook: BeforeContinuationHook) -> u64 {
    let id = BEFORE_CONTINUATION_COUNTER.fetch_add(1, Ordering::SeqCst);
    get_hooks()
        .write()
        .unwrap()
        .before_continuation
        .push((id, hook));
    id
}

pub fn register_before_execute_hook(hook: BeforeExecuteHook) -> u64 {
    let id = BEFORE_EXECUTE_COUNTER.fetch_add(1, Ordering::SeqCst);
    get_hooks().write().unwrap().before_execute.push((id, hook));
    id
}

pub fn register_after_execute_hook(hook: AfterExecuteHook) -> u64 {
    let id = AFTER_EXECUTE_COUNTER.fetch_add(1, Ordering::SeqCst);
    get_hooks().write().unwrap().after_execute.push((id, hook));
    id
}

/// Unregister hooks by ID - returns true if hook was found and removed
pub fn unregister_before_prompt_hook(id: u64) -> bool {
    let mut hooks = get_hooks().write().unwrap();
    if let Some(pos) = hooks
        .before_prompt
        .iter()
        .position(|(hook_id, _)| *hook_id == id)
    {
        let _ = hooks.before_prompt.remove(pos);
        true
    } else {
        false
    }
}

pub fn unregister_before_continuation_hook(id: u64) -> bool {
    let mut hooks = get_hooks().write().unwrap();
    if let Some(pos) = hooks
        .before_continuation
        .iter()
        .position(|(hook_id, _)| *hook_id == id)
    {
        let _ = hooks.before_continuation.remove(pos);
        true
    } else {
        false
    }
}

pub fn unregister_before_execute_hook(id: u64) -> bool {
    let mut hooks = get_hooks().write().unwrap();
    if let Some(pos) = hooks
        .before_execute
        .iter()
        .position(|(hook_id, _)| *hook_id == id)
    {
        let _ = hooks.before_execute.remove(pos);
        true
    } else {
        false
    }
}

pub fn unregister_after_execute_hook(id: u64) -> bool {
    let mut hooks = get_hooks().write().unwrap();
    if let Some(pos) = hooks
        .after_execute
        .iter()
        .position(|(hook_id, _)| *hook_id == id)
    {
        let _ = hooks.after_execute.remove(pos);
        true
    } else {
        false
    }
}

/// List hook IDs in registration order
pub fn list_before_prompt_hook_ids() -> Vec<u64> {
    get_hooks()
        .read()
        .unwrap()
        .before_prompt
        .iter()
        .map(|(id, _)| *id)
        .collect()
}

pub fn list_before_continuation_hook_ids() -> Vec<u64> {
    get_hooks()
        .read()
        .unwrap()
        .before_continuation
        .iter()
        .map(|(id, _)| *id)
        .collect()
}

pub fn list_before_execute_hook_ids() -> Vec<u64> {
    get_hooks()
        .read()
        .unwrap()
        .before_execute
        .iter()
        .map(|(id, _)| *id)
        .collect()
}

pub fn list_after_execute_hook_ids() -> Vec<u64> {
    get_hooks()
        .read()
        .unwrap()
        .after_execute
        .iter()
        .map(|(id, _)| *id)
        .collect()
}

/// Fire hooks
fn fire_before_prompt_hooks() {
    let hooks = get_hooks().read().unwrap();
    for (_id, hook) in &hooks.before_prompt {
        hook();
    }
}

fn fire_before_continuation_hooks(prev_prompt: &str, buffer: &str) {
    let hooks = get_hooks().read().unwrap();
    for (_id, hook) in &hooks.before_continuation {
        hook(prev_prompt, buffer);
    }
}

fn fire_before_execute_hooks(command: &str) {
    let hooks = get_hooks().read().unwrap();
    for (_id, hook) in &hooks.before_execute {
        hook(command);
    }
}

fn fire_after_execute_hooks(command: &str) {
    let hooks = get_hooks().read().unwrap();
    for (_id, hook) in &hooks.after_execute {
        hook(command);
    }
}

/// Custom prompt for ShipShell
struct ShipPrompt {
    is_continuation: bool,
}

impl ShipPrompt {
    fn new() -> Self {
        Self {
            is_continuation: false,
        }
    }
}

impl Prompt for ShipPrompt {
    fn render_prompt_left(&self) -> Cow<'_, str> {
        let repl_state = get_repl_state().read().unwrap();
        // Use ANSI reset code to ensure white/default terminal color
        if self.is_continuation {
            Cow::Owned(format!("\x1b[0m{}", repl_state.continuation_prompt))
        } else {
            Cow::Owned(format!("\x1b[0m{}", repl_state.primary_prompt))
        }
    }

    fn render_prompt_right(&self) -> Cow<'_, str> {
        let repl_state = get_repl_state().read().unwrap();
        Cow::Owned(format!("\x1b[0m{}", repl_state.right_prompt))
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

/// Check if a Python statement is complete
/// This function is passed in to avoid Python dependency in REPL module
type StatementChecker = Box<dyn Fn(&str) -> bool + Send + Sync>;
static STATEMENT_CHECKER: OnceLock<StatementChecker> = OnceLock::new();

pub fn set_statement_checker(checker: StatementChecker) {
    STATEMENT_CHECKER.set(checker).ok();
}

fn is_complete_statement(code: &str) -> bool {
    if let Some(checker) = STATEMENT_CHECKER.get() {
        checker(code)
    } else {
        // If no checker registered, assume complete to avoid blocking
        true
    }
}

/// Executor function type - executes code and sets ? environment variable
type CodeExecutor = Box<dyn Fn(&str) -> anyhow::Result<()> + Send + Sync>;
static CODE_EXECUTOR: OnceLock<CodeExecutor> = OnceLock::new();

pub fn set_code_executor(executor: CodeExecutor) {
    CODE_EXECUTOR.set(executor).ok();
}

/// Main REPL loop - completely Python-agnostic
pub fn run() -> anyhow::Result<()> {
    // Create reedline editor (default: white text, no syntax highlighting)
    let mut line_editor = Reedline::create();
    let mut buffer = String::new();
    let mut prompt = ShipPrompt::new();

    println!("ShipShell Python REPL");
    println!("Type 'exit()' or press Ctrl+D to quit");
    println!();

    let mut prev_prompt = get_primary_prompt();

    loop {
        // Update prompt state
        prompt.is_continuation = !buffer.is_empty();

        // Fire appropriate hook before rendering prompt
        if prompt.is_continuation {
            fire_before_continuation_hooks(&prev_prompt, &buffer);
        } else {
            fire_before_prompt_hooks();
            prev_prompt = get_primary_prompt();
        }

        let sig = line_editor.read_line(&prompt);

        match sig {
            Ok(Signal::Success(line)) => {
                // Append line to buffer
                if !buffer.is_empty() {
                    buffer.push('\n');
                }
                buffer.push_str(&line);

                // Check if statement is complete
                if is_complete_statement(&buffer) {
                    // Skip empty statements
                    if !buffer.trim().is_empty() {
                        // Fire before execute hook
                        fire_before_execute_hooks(&buffer);

                        // Execute code via registered executor
                        if let Some(executor) = CODE_EXECUTOR.get()
                            && let Err(e) = executor(&buffer)
                        {
                            eprintln!("Error executing code: {}", e);
                        }

                        // Fire after execute hook
                        fire_after_execute_hooks(&buffer);
                    }

                    // Clear buffer for next statement
                    buffer.clear();
                }
            }
            Ok(Signal::CtrlC) => {
                println!("^C");
                buffer.clear();
                continue;
            }
            Ok(Signal::CtrlD) => {
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
