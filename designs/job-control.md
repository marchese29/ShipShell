# Job Control Design

## Overview

ShipShell's job control system provides Pythonic, composable background process
management. Jobs are first-class Python objects with completion handlers, file-backed
output capture, and seamless foreground/background transitions.

The design diverges from bash's stringly-typed, implicit model (`%1`, `&`, `fg`, `bg`)
in favor of explicit Python values and composition.

## Core Principles

1. **Jobs are values, not magic** — `bg()` returns a `Job` handle you can store, pass,
   compose, and inspect.
2. **Output never clobbers** — all process output is captured to files. Foreground
   display is a separate concern handled by the parent.
3. **Composition over management** — `on_exit()` and `on_error()` let you build
   workflows declaratively instead of babysitting processes.
4. **Fork-safe, no threads** — the architecture uses only forked processes and
   `select()`-based event loops. No threads means every `os.fork()` in the system
   remains safe.
5. **Uniform PTY model** — every job gets PTYs from the start, regardless of whether
   it begins in the foreground or background. This eliminates asymmetry between
   bg-started and fg-started jobs and enables seamless transitions in both directions.

## Architecture

### Layer Model

```
Layer 4: Job API  (bg, fg, Job, JobTable, on_exit, on_error, send_input)
Layer 3: File-backed Output  (PTY + drain/proxy for all jobs)
Layer 2: Terminal Control  (tcsetpgrp — stdin access, signal routing)
Layer 1: Process Groups  (setpgid — group related processes into jobs)
```

Each layer is independent. POSIX terminal control (layers 1-2) handles signal routing
and stdin access. File-backed output (layer 3) handles where stdout/stderr data goes.
The Python API (layer 4) wraps it all.

### Key Insight: Foreground is the Special Case

There can only be one foreground job at a time. This means:

- At most one PTY proxy is active at any moment
- The parent process itself acts as the proxy (no extra processes or threads)
- Background is the common mode (drain processes handle PTY → file)
- Foreground adds the display layer on top (parent reads PTY → terminal + file)

## Output Model

### Every Job Gets PTYs

Every external process launched by ShipShell gets two PTYs (one for stdout, one for
stderr) and has its output captured to files. No process ever writes directly to the
real terminal. The only difference between foreground and background is **who reads the
PTY masters**: the parent (foreground, also displaying to terminal) or drain processes
(background, file only).

```
Every job:
  child fd 0 ←── PTY₁ slave ←── PTY₁ master  (stdin)
  child fd 1 ──→ PTY₁ slave ──→ PTY₁ master  (stdout)
  child fd 2 ──→ PTY₂ slave ──→ PTY₂ master  (stderr)

Foreground: parent reads PTY masters → real terminal + capture files
Background: drain processes read PTY masters → capture files only
```

This uniform model means:

- **`isatty()` is true for every job** — colors, progress bars, and raw mode work
  regardless of whether the job is foreground or background
- **bg→fg transitions have full fidelity** — the child already has PTYs, so
  foregrounding just swaps the drain processes for the parent's proxy loop
- **fg→bg transitions are clean** — the parent stops proxying, drain processes take
  over, no output is lost
- **One setup path** — no "bg path" vs "fg path" branching in job creation
- **Full interactive fg()** — because stdin is a real PTY, foregrounded jobs get
  echo, line editing (readline in the child), and signal generation via the PTY line
  discipline

### Background Jobs and stdin

Because every job has a PTY on stdin (`isatty(0) == true`), programs that check
stdin's tty status will believe they are interactive. Shells will show a prompt. Python
will enter REPL mode. This is a deliberate choice.

In practice, background jobs that try to read stdin simply **block** on the PTY slave
read — nobody is writing to the PTY master, so there's no data. This is functionally
equivalent to bash's SIGTTIN behavior:

| | bash `cmd &` | ShipShell `bg(cmd)` |
|--|--|--|
| Process reads stdin | SIGTTIN → stopped | Blocks on PTY read (idle) |
| CPU usage | None | None |
| Job table shows | `stopped` | `running` (waiting for input) |
| `fg()` behavior | SIGCONT → resumes read | Parent proxies stdin → read unblocks |

Both consume zero CPU. Both resume cleanly with `fg()`. The difference is cosmetic —
SIGTTIN stops the process at the OS level, while our model lets it block in a read
syscall. The end result is identical from the user's perspective.

SIGTTIN is specific to the "real terminal" model — it fires when a background process
group reads from the controlling terminal. In our model, each job's controlling
terminal is its own PTY, and the job is always the foreground group on that PTY. So
SIGTTIN never enters the picture. No mainstream programs build features on catching
SIGTTIN — it almost universally takes the default action (stop).

The one quirk: programs that use `isatty(0)` to decide whether to be interactive will
see `true` and enter interactive mode (showing prompts, enabling REPLs). In practice
this is harmless — the process blocks on read and waits. Well-written programs offer
`--no-interactive` or `-n` flags to override this detection. This is a documented
side-effect of the uniform PTY model, and a small price for full interactive
resumption, `send_input()`, built-in `expect`-like interaction, and seamless fg/bg
transitions.

### Builtins and InProcessCallable

Builtins and `InProcessCallable` run directly in the parent process (as they do today)
because they need to modify parent state — `cd` changes the working directory,
`export` modifies the environment, user callables may access shared Python objects.
Their output goes to the real terminal directly and is not captured to files. This is
acceptable because builtins are fast, produce small output, and their primary value is
their side effects, not their output.

When backgrounded via `bg()`, builtins and callables are forked into a subshell (like
all backgrounded runnables). In the subshell they get PTYs and capture files like any
other job, but they cannot modify parent state — backgrounding means isolation.

### Two PTYs Per Job

Each job uses separate PTYs for stdout and stderr rather than multiplexing both
through a single PTY:

- **No interleaving** — each stream has its own line discipline, so a partial stdout
  write won't be interrupted by stderr data mid-line
- **Separate capture files** — `job.stdout` and `job.stderr` are cleanly separated
- **Consistent API** — `job.stdout_path` and `job.stderr_path` always exist and
  contain only their respective stream

The child's stdin is connected to the stdout PTY's slave end (PTY₁), since stdin and
stdout traditionally share the same terminal device. The parent (or `send_input()`)
writes to PTY₁'s master end to deliver input to the child.

### PTY Internals

A PTY is a kernel-managed bidirectional channel that looks like a terminal to the
child but looks like a regular fd to the parent:

```
PTY slave  (child side)  ←── kernel ──→  PTY master (parent side)
  looks like /dev/ttyN                     regular readable/writable fd
  isatty() = true                          parent reads child output here
  supports termios, raw mode               parent writes child input here
  handles line discipline
```

The child writes to the slave end. The kernel passes bytes through the PTY's line
discipline (which handles `\n` → `\r\n` conversion, echo, signal generation from
Ctrl+C/Z). The bytes emerge from the master end, where the parent (or a drain process)
reads them. The child cannot write to both a file and the terminal through a single
fd — the PTY is the thing it writes to, and the master-end reader decides where the
data goes.

### ANSI Escapes in Capture Files

Because every job has `isatty() == true`, programs emit ANSI color codes and escape
sequences into their output. These are captured faithfully to the output files:

- `cat job.stdout_path` in a terminal renders the colors (useful for review)
- `job | grep('pattern')` works fine (grep handles ANSI gracefully)
- For programmatic processing, use the stripped accessor:

```python
job.stdout              # raw output (with ANSI escapes)
job.stdout_text         # stripped plain text (ANSI removed)
job.stderr              # raw stderr
job.stderr_text         # stripped stderr
```

This matches the trade-off `script(1)` makes — preserving fidelity is better than
losing information.

### File Management

Job output files live in a managed directory:

```
~/.config/pysh/job_output/
  job_001_stdout.log
  job_001_stderr.log
  job_002_stdout.log
  ...
```

Cleanup policy is configurable via `env.settings.job_output_retain`:

| Setting        | Behavior                                         |
|----------------|--------------------------------------------------|
| `'session'`    | Delete all output files when REPL exits          |
| `'manual'`     | Keep forever, user cleans up                     |
| `'1h'`/`'24h'` | Delete files older than the specified duration  |

### Late Piping (Output)

Because output is always in a file, you can compose with a job's output after it
started — or even after it finished:

```python
job = bg(find('/', '-name', '*.log'))
# ... time passes ...
job | grep('error') | sort()     # reads from job's output file
job | tail('-20')                # last 20 lines
```

`job | cmd` constructs a pipeline that reads from `job.stdout_path`.

### Late Piping (Input)

Because every job has a stdin PTY master, you can pipe input into a running job:

```python
job = bg(my_server())
echo('hello') | job              # pipe echo's output to job's stdin
cat('commands.txt') | job        # feed a file to a running process
```

`cmd | job` runs the left-hand command, captures its output, and writes it to the
job's stdin PTY master. This also works with direct string input:

```python
job.send_input('hello world\n')  # write directly to stdin PTY master
```

### Programmatic Process Interaction (Built-in `expect`)

The combination of `wait_for()` + `send_input()` gives ShipShell a built-in equivalent
to `expect` / `pexpect` — you can script interactive programs without external tools:

```python
installer = bg(prog('./install.sh')())
installer.wait_for('Accept license?')
installer.send_input('yes\n')
installer.wait_for('Install directory:')
installer.send_input('/opt/myapp\n')
installer.wait_for('Complete')
installer.wait()
```

Background jobs that try to read input simply block on the PTY slave (waiting for data
on the master end). When you `fg()` the job, the parent starts forwarding keystrokes
and the blocked read unblocks naturally. When you `send_input()`, the data appears on
the master end and the blocked read unblocks. No special signals or mechanisms needed
— this is just how PTYs work.

## Foreground Event Loop

The parent replaces the current blocking `waitpid(pid, 0)` with a `select()`-based
loop that proxies I/O between the real terminal and the PTYs, while capturing output
to files:

```python
saved_termios = termios.tcgetattr(real_stdin)
tty.setraw(real_stdin)

# Self-pipe trick: turn SIGCHLD into an fd event
sig_r, sig_w = os.pipe()
os.set_blocking(sig_w, False)
signal.signal(signal.SIGCHLD, lambda *_: os.write(sig_w, b'\x00'))

try:
    sel = selectors.DefaultSelector()
    sel.register(real_stdin_fd, selectors.EVENT_READ, 'stdin')
    sel.register(pty1_master_fd, selectors.EVENT_READ, 'stdout')
    sel.register(pty2_master_fd, selectors.EVENT_READ, 'stderr')
    sel.register(sig_r, selectors.EVENT_READ, 'signal')

    while True:
        for key, _ in sel.select():
            if key.data == 'stdin':
                # User typed — forward to child via PTY₁
                data = os.read(real_stdin_fd, 128)
                os.write(pty1_master_fd, data)

            elif key.data == 'stdout':
                # Child stdout — display and capture
                data = os.read(pty1_master_fd, 65536)
                if data:
                    os.write(real_stdout_fd, data)
                    stdout_file.write(data)

            elif key.data == 'stderr':
                # Child stderr — display and capture separately
                data = os.read(pty2_master_fd, 65536)
                if data:
                    os.write(real_stderr_fd, data)
                    stderr_file.write(data)

            elif key.data == 'signal':
                os.read(sig_r, 256)  # drain self-pipe
                pid, status = os.waitpid(child_pid, os.WUNTRACED | os.WNOHANG)
                if pid != 0:
                    if os.WIFSTOPPED(status):
                        return 'stopped'
                    elif os.WIFEXITED(status) or os.WIFSIGNALED(status):
                        # Drain remaining PTY output
                        for master, capture in [
                            (pty1_master_fd, stdout_file),
                            (pty2_master_fd, stderr_file),
                        ]:
                            while (data := os.read(master, 65536)):
                                capture.write(data)
                        return os.waitstatus_to_exitcode(status)
finally:
    termios.tcsetattr(real_stdin, termios.TCSADRAIN, saved_termios)
    os.tcsetpgrp(real_stdin_fd, shell_pgid)
```

The real terminal is put into raw mode so keystrokes pass through transparently to the
PTY (including Ctrl characters and escape sequences). The PTY slave's line discipline
handles interpretation — if the child has the PTY in cooked mode, Ctrl+C generates
SIGINT; if in raw mode, the byte passes through as data.

### Latency

The PTY proxy adds negligible overhead. The data path is:

```
child write() → PTY slave → kernel → PTY master → parent read() → parent write() → terminal
```

Two extra user-space crossings compared to direct terminal write. In practice:

- `os.read()` + `os.write()` round-trip: ~1-5 microseconds
- Terminal emulator rendering: ~50-200 microseconds
- Human perception threshold: ~10-50 milliseconds

The proxy adds single-digit microseconds against a backdrop where the terminal
emulator is orders of magnitude slower. `screen` and `tmux` use this exact
architecture for every byte of I/O and are imperceptible.

The PTY actually improves perceived latency for some programs: with a PTY, programs
see `isatty() == true` and use line-buffered output. Without one (pipe or file), they
switch to block-buffered (4-8KB chunks), making output appear in delayed bursts.

### Raw Mode and Interactive Programs

Because the child's fds point to PTY slaves:

- `isatty(1)` → true — colors, progress bars, raw mode all work
- `tcsetattr()` on the PTY slave modifies the child's terminal settings without
  affecting the real terminal
- SIGWINCH: parent forwards window size changes to the PTYs via `TIOCSWINSZ` ioctl

When the child has the PTY in raw mode (vim, less), Ctrl+Z does not generate SIGTSTP
through the PTY line discipline — the byte passes as data. This matches `screen`/`tmux`
behavior. For raw-mode programs, job suspension requires a shell escape mechanism (TBD
— possibly double Ctrl+Z or a configurable escape key).

### Portability

The foreground loop uses only portable POSIX mechanisms:

| Need                    | Mechanism                          | Portable     |
|-------------------------|------------------------------------|--------------|
| Watch PTY masters       | `select()` / `selectors`           | POSIX        |
| Watch stdin             | `select()` / `selectors`           | POSIX        |
| Detect child exit/stop  | SIGCHLD + self-pipe trick          | POSIX        |
| PTY allocation          | `os.openpty()` / `os.forkpty()`    | Python stdlib|
| Process groups          | `os.setpgid()`                     | POSIX        |
| Terminal control        | `os.tcsetpgrp()`, `termios`        | POSIX        |
| Raw mode                | `tty.setraw()` / `termios`         | Python stdlib|

No platform-specific APIs (kqueue, epoll, inotify) are required for core
functionality.

## REPL Event Loop (Idle)

When the REPL is waiting for user input, the event loop is minimal — it only watches
stdin and the SIGCHLD self-pipe:

```python
sel = selectors.DefaultSelector()
sel.register(stdin_fd, selectors.EVENT_READ, 'stdin')
sel.register(sig_r, selectors.EVENT_READ, 'signal')

for key, _ in sel.select():
    if key.data == 'stdin':
        rl.read_char()
    elif key.data == 'signal':
        os.read(sig_r, 256)
        _reap_completed_jobs()
```

Two fds. Always two fds, regardless of how many background jobs exist. The REPL stays
perfectly responsive because all background PTY draining is handled by dedicated drain
processes.

## Drain Processes

Every background job has two **drain processes** — small forked children that read PTY
masters and write to capture files:

```python
def _start_drain(pty_master_fd: int, output_path: Path) -> int:
    """Fork a process that drains a PTY master to a file."""
    pid = os.fork()
    if pid == 0:
        with open(output_path, 'ab') as f:
            while True:
                data = os.read(pty_master_fd, 65536)
                if not data:
                    break  # PTY closed (child exited)
                f.write(data)
        os._exit(0)
    return pid
```

These are trivial processes — just `read()` → `write()` in a loop — that run
completely independently of the REPL. Two drain processes per background job (one for
stdout, one for stderr).

### Drain Lifecycle

```
bg() creates a new job:
    allocate PTY₁ and PTY₂
    fork job child (fds → PTY slaves)
    fork drain process for PTY₁ master → stdout file
    fork drain process for PTY₂ master → stderr file
    job.drain_pids = [drain1_pid, drain2_pid]
    return Job handle immediately

fg(job):
    parent kills drain processes (SIGTERM + waitpid)
    parent enters proxy loop (reads PTY masters → terminal + files)

bg(fg_job) or Ctrl+Z → bg():
    parent exits proxy loop
    parent forks new drain processes (PTY masters → files)
    parent returns to REPL

Job completes while bg'd:
    child exits → PTY slaves close → PTY masters get EOF
    drain processes read EOF → exit naturally
    parent receives SIGCHLD for job + drains → reaps all
```

The drain PIDs are tracked on the `Job` object so the parent knows to kill them during
`fg()` and to ignore them (not treat as real jobs) during SIGCHLD reaping.

### Cost

Two drain processes per background job. For a user with 5 background jobs, that's 10
extra processes. Each drain process is a minimal `read()`/`write()` loop with
negligible memory and CPU usage. The OS handles thousands of processes routinely —
this is not a meaningful cost.

## Process Groups

Every top-level job gets its own process group:

```python
# In the child, after fork, before exec:
os.setpgid(0, 0)   # pgid = own pid

# For pipeline stages: join the leader's group
os.setpgid(0, pipeline_leader_pid)
```

The shell maintains its own process group and reclaims terminal ownership when
returning to the REPL:

```python
# Shell startup:
shell_pgid = os.getpgrp()

# Before foregrounding a job:
os.tcsetpgrp(terminal_fd, job.pgid)

# After job completes or suspends:
os.tcsetpgrp(terminal_fd, shell_pgid)
```

### What Process Groups Provide

- **Signal routing**: Ctrl+C → SIGINT to foreground group, Ctrl+Z → SIGTSTP to
  foreground group (both via the PTY line discipline when in cooked mode)
- **stdin access**: only the foreground group can read from terminal (background
  readers get SIGTTIN)
- **Job-level control**: `os.killpg(pgid, SIGCONT)` resumes all processes in a job

Process groups are orthogonal to the output model. They handle input and signals;
file-backed output handles where stdout/stderr data goes.

## Job Table

The job table is a property of `ShellEnvironment`:

```python
env.jobs          # JobTable — iterable, indexable
env.jobs[1]       # Job by ID
env.jobs.last     # Most recently created/suspended job
env.jobs.running  # Filter: currently running jobs
env.jobs.stopped  # Filter: suspended jobs
```

The job table lives on `env` (not `os.environ`) because it contains rich Python
objects. This follows the existing pattern — `env` already diverges from `os.environ`
with `Path` objects in `env.path`, copy-on-read semantics, and computed properties.

### Job Lifecycle

```
           bg()              fg()
            │                  │
            ▼                  ▼
  ┌──────────────┐    ┌──────────────┐
  │   running    │◄──►│  foreground   │
  │  (bg+drains) │    │  (pty proxy)  │
  └──────┬───────┘    └──────┬───────┘
         │                   │
         │   Ctrl+Z          │
         │   ◄───────────────┘
         │         │
         ▼         ▼
  ┌──────────────┐
  │   stopped    │
  └──────┬───────┘
         │
    exit/signal
         │
         ▼
  ┌──────────────┐
  │  completed   │
  └──────────────┘
```

All transitions are fully symmetric. A bg-started job can be foregrounded with full
terminal fidelity. A fg-started job can be backgrounded cleanly. The PTYs are
allocated once at job creation and persist through every state transition.

States:
- **running (background)**: child executing, drain processes handling PTY → file
- **foreground**: child executing, parent proxying PTYs → terminal + files
- **stopped**: child suspended (SIGTSTP), waiting for fg/bg
- **completed**: child exited, output files finalized, handlers fired

## Job Class

```python
class Job:
    # Identity
    id: int                     # job table index
    name: str                   # display name ("make -j8")
    pgid: int                   # process group ID
    pid: int                    # leader PID

    # State
    status: JobStatus           # running | stopped | completed | failed
    exit_code: int | None       # None until completed

    # Output
    stdout_path: Path           # path to captured stdout file
    stderr_path: Path           # path to captured stderr file
    stdout: str                 # property — reads stdout file (raw, with ANSI)
    stderr: str                 # property — reads stderr file (raw, with ANSI)
    stdout_text: str            # property — reads stdout file (ANSI stripped)
    stderr_text: str            # property — reads stderr file (ANSI stripped)

    # PTY master fds (always present for external command jobs)
    pty_stdout_master: int      # PTY₁ master fd (stdout + stdin)
    pty_stderr_master: int      # PTY₂ master fd (stderr)

    # Drain processes (active when job is in background)
    drain_pids: list[int]

    # Handlers
    _on_exit: list[ShellRunnable]
    _on_error: list[ShellRunnable]

    # Output composition
    def __or__(self, other) -> Pipeline: ...       # job | cmd (late pipe output)
    def __ror__(self, other) -> ShellResult: ...   # cmd | job (late pipe input)

    # Input
    def send_input(self, data: str) -> None: ...   # write to stdin PTY master

    # Lifecycle
    def wait(self) -> int: ...
    def wait_for(self, predicate, *, timeout=None) -> bool: ...
    def on_exit(self, handler: ShellRunnable) -> Job: ...
    def on_error(self, handler: ShellRunnable) -> Job: ...
    def kill(self, signal=SIGTERM) -> None: ...

    # Iteration
    def __iter__(self) -> Iterator[str]: ...       # iterate stdout lines
```

## Completion Handlers

`on_exit()` and `on_error()` schedule successor tasks that run when the job completes:

```python
bg(make('-j8')) \
    .on_exit(make('install')) \
    .on_error(notify('Build failed'))
```

When the job table reaps a completed job:

1. Check exit code → select `on_exit` (code 0) or `on_error` (code != 0) handlers
2. For each handler: fork a new background job (with PTYs and drains)
3. The new job gets its own entry in the job table
4. In the forked child's environment, set `env.predecessor` with context from the
   triggering job:

```python
# Available only in the handler's forked environment:
env.predecessor.exit_code       # int
env.predecessor.stdout_path     # Path to completed job's stdout file
env.predecessor.stderr_path     # Path to completed job's stderr file
env.predecessor.name            # "make -j8"
env.predecessor.job_id          # 1
```

Handlers run in the background and cannot block the REPL. They are ordinary
background jobs with extra context — scheduled by the job table rather than by the
user.

### Chaining

`on_exit()` and `on_error()` return the Job for fluent chaining:

```python
job = bg(make('-j8')) \
    .on_exit(make('install')) \
    .on_exit(notify('done')) \
    .on_error(notify('failed'))
```

Multiple handlers on the same event all fire (fan-out).

## User-Facing API

### `bg(runnable) -> Job`

Background a runnable. Returns immediately with a Job handle.

```python
job = bg(make('-j8'))                             # command
job = bg(find('.') | grep('TODO'))                # pipeline
job = bg(sub(cd('/tmp') | tar('xzf', 'a.gz')))   # subshell
job = bg(my_python_function)                      # callable (forked)
```

All runnables are forked into a subshell when backgrounded. `InProcessCallable` and
builtins are forked so they never execute in the main event loop. This means
backgrounded builtins cannot modify parent state — `bg(cd('/tmp'))` changes the
child's directory, not the parent's. This is intentional: backgrounding means
isolation.

Every bg job gets PTYs and drain processes from the start.

### `fg(job_or_id) -> int`

Foreground a background/stopped job. Blocks until the job completes or is suspended.

```python
fg(1)          # by job ID
fg(job)        # by Job handle
fg()           # most recent job (env.jobs.last)
```

Kills the job's drain processes, then the parent enters the PTY proxy loop with full
terminal fidelity — colors, raw mode, interactive I/O. Works identically regardless of
whether the job was started with `bg()` or `run()`.

Returns the exit code if the job completed.

### `bg(stopped_job) -> Job`

Resume a stopped job in the background:

```python
bg(env.jobs.last)   # resume most recent stopped job
bg(1)               # resume by ID
```

Forks new drain processes for the job's PTY masters and sends SIGCONT.

### `jobs() -> JobTable`

Display and return the job table:

```python
>>> jobs()
 Job  PID    Status     Command
  1   12345  running    make -j8
  2   12348  stopped    vim server.py
  3   12350  completed  pytest tests/ (exit 0)
```

The return value is the `JobTable` itself — queryable and indexable.

### Context Manager

Background jobs can be scoped to a `with` block for automatic cleanup:

```python
with bg(prog('./server')('--port=8080')) as server:
    server.wait_for('Listening on port 8080')
    pytest('tests/integration/')()
# server automatically killed + waited on __exit__
```

### `send_input(data)`

Write directly to a job's stdin PTY master:

```python
job = bg(my_server())
job.send_input('hello world\n')
```

If the child is blocked on `read()`, the data appears on the PTY slave and the read
unblocks. If the child isn't reading, the data buffers in the PTY until it does.

Combined with the `__ror__` operator for piping input:

```python
echo('hello') | job              # pipe command output to job's stdin
cat('commands.txt') | job        # feed a file to a running process
```

### `wait_for(predicate, *, timeout=None)`

Block until a condition is met on a job's output:

```python
job.wait_for()                                # any stdout output
job.wait_for('Listening on port')             # string appears in stdout
job.wait_for(lambda content: 'ready' in content)  # predicate
job.wait_for('pattern', timeout=30)           # with timeout
```

Implementation uses platform-specific file monitoring to avoid polling:

| Platform | Mechanism                              |
|----------|----------------------------------------|
| macOS    | `kqueue` with `KQ_FILTER_VNODE`        |
| Linux    | `inotify` with `IN_MODIFY`             |

## Foreground/Background Transitions

All transitions are symmetric because every job has PTYs from creation.

### bg() from the start

```
allocate PTY₁ (stdout/stdin) and PTY₂ (stderr)
create stdout and stderr capture files
fork job child
  child: setsid(); dup2(pty1_slave, 0/1); dup2(pty2_slave, 2);
         setpgid(0,0); exec(cmd)
fork drain process for PTY₁ master → stdout file
fork drain process for PTY₂ master → stderr file
record Job in table, return handle immediately
```

### run() foreground (PTY proxy path)

```
allocate PTY₁ (stdout/stdin) and PTY₂ (stderr)
create stdout and stderr capture files
fork job child
  child: setsid(); dup2(pty1_slave, 0/1); dup2(pty2_slave, 2);
         setpgid(0,0); exec(cmd)
parent:
  save real terminal settings; set raw mode
  tcsetpgrp(terminal, child_pgid)
  enter select() proxy loop:
    real stdin → PTY₁ master (forward keystrokes)
    PTY₁ master → real stdout + stdout file (display and capture)
    PTY₂ master → real stderr + stderr file (display and capture)
  until: child exits or stops
  restore terminal settings; tcsetpgrp(terminal, shell_pgid)
```

Builtins and `InProcessCallable` bypass this entirely — they run directly in the
parent process without forking, as they do today.

### Ctrl+Z (foreground → stopped)

```
child receives SIGTSTP (via PTY line discipline in cooked mode)
child stops
parent receives SIGCHLD (WIFSTOPPED) via self-pipe
parent: exits proxy loop
parent: restores terminal settings
parent: tcsetpgrp(terminal, shell_pgid)
parent: creates/updates Job entry (status=stopped, retains PTY master fds)
parent: returns to REPL
```

### fg(job) (background/stopped → foreground)

```
parent: kills drain processes if running (SIGTERM + waitpid)
parent: saves terminal settings; sets raw mode
parent: tcsetpgrp(terminal, job.pgid)
parent: os.killpg(job.pgid, SIGCONT)  (if stopped)
parent: enters proxy loop (reads both PTY masters → terminal + files)
```

Output files continue from where they left off. Terminal display resumes with full
fidelity regardless of whether the job was started with `bg()` or `run()`.

### bg(job) (foreground/stopped → background)

```
parent: exits proxy loop (if currently foregrounded)
parent: os.killpg(job.pgid, SIGCONT)  (if stopped)
parent: forks drain process for PTY₁ master → stdout file
parent: forks drain process for PTY₂ master → stderr file
parent: job.drain_pids = [drain1, drain2]
parent: returns to REPL
```

### The full round-trip: run() → Ctrl+Z → bg() → fg()

This transition chain works seamlessly because PTYs are permanent:

```
run()    → child gets PTY slaves for fd 0/1/2. isatty = true. PTYs allocated.
              Parent proxies: PTY masters → terminal + files.

Ctrl+Z   → child stops. Parent exits proxy loop, restores terminal.
              Job created with PTY master fds retained.

bg()     → parent sends SIGCONT. Child resumes, still writing to PTY slaves.
              Drain processes forked (PTY masters → files). REPL stays clean.

fg()     → parent kills drain processes.
              Parent re-enters proxy loop. PTY masters → terminal + files.
              Raw mode on real terminal. tcsetpgrp to child's group.
              Full terminal fidelity — colors, raw mode, everything.
```

The child's fd 1 points at the same PTY₁ slave through every transition. It never
changes. `isatty()` is true the entire time.

## Job Notifications

When the REPL is idle and a background job completes, a notification prints between
prompts:

```
>>> ls()
file1.py  file2.py
>>>
[1] done    make -j8          (exit 0)
[2] failed  deploy --prod     (exit 1) -- stderr: 12 lines
>>>
```

Notifications print after reaping a completed job and before re-displaying the prompt.
`rl.redisplay()` ensures the prompt reprints cleanly.

### Stderr Notification (opt-in)

By default, stderr goes silently to a file. An opt-in setting shows a notice when a
background job produces stderr:

```python
env.settings.job_stderr_notify = True
```

The full stderr is always available via `job.stderr` or `job.stderr_path`.

## Scope and Non-Goals

### In Scope
- `bg()`, `fg()`, `jobs()` builtins
- `Job` class with completion handlers (`on_exit`, `on_error`)
- `JobTable` on `env.jobs`
- Uniform dual-PTY model for all jobs (foreground and background)
- Drain processes for background PTY management
- File-backed output capture with ANSI-preserving and stripped accessors
- Process groups and terminal control
- Ctrl+Z suspend/resume
- `send_input()` and late input piping (`cmd | job`)
- Built-in `expect`-like interaction (`wait_for` + `send_input`)
- Context manager for scoped background processes
- Late output piping (`job | cmd`)
- Job completion notifications
- `wait_for()` with platform-specific file monitoring

### Not in Scope
- Multiple terminal panes/windows (tmux-style multiplexing)
- Job scheduling/queuing (cron-like)
- Remote/distributed job execution
- Persistent jobs across shell sessions

## Implementation Order

### Phase 1: Foundation
- Process group management (`setpgid` for all forked children)
- Terminal control (`tcsetpgrp` for foreground/shell switching)
- `Job` class and `JobTable` on `env`
- SIGCHLD handling with self-pipe trick in REPL event loop
- Output file management (directory, creation, cleanup)

### Phase 2: PTY Infrastructure and Background Jobs
- Dual PTY allocation per job
- Drain process forking and lifecycle management
- `bg()` function — fork into subshell with PTYs, start drains, return Job
- Job reaping and notifications in REPL idle loop
- `jobs()` builtin
- `job.wait()`, `job.kill()`, `job.stdout`, `job.stderr`
- `job.stdout_text`, `job.stderr_text` (ANSI stripping)
- Late output piping (`job | cmd`)

### Phase 3: Foreground PTY Proxy
- Foreground proxy loop (stdin forwarding, stdout/stderr capture)
- Raw terminal mode management (save/restore)
- SIGWINCH forwarding
- Modify `run()` to use PTY proxy for top-level external commands

### Phase 4: Job Transitions
- Ctrl+Z handling (SIGTSTP → stopped state)
- `fg()` — kill drains, enter proxy loop
- `bg()` for stopped/foreground jobs — fork drains, resume
- Terminal ownership transitions (`tcsetpgrp` dance)

### Phase 5: Composition and Interaction
- `on_exit()` / `on_error()` completion handlers
- `env.predecessor` context in handler children
- Context manager (`with bg(cmd) as job:`)
- `send_input()` — write to stdin PTY master
- Late input piping (`cmd | job` via `__ror__`)
- `wait_for()` with file monitoring adapters

### Phase 6: Polish
- Job output directory configuration
- Cleanup policies (session, timed, manual)
- Stderr notification setting
- Error handling and edge cases
- Integration with existing trap system (SIGCHLD trap, job-aware DEBUG/TRACE)
- Shell escape mechanism for suspending raw-mode programs
