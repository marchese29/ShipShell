use pyo3::prelude::*;
use std::sync::Arc;

use crate::shell_env;

#[pymodule]
pub mod shp {
    use super::*;

    #[pyclass]
    #[derive(Clone)]
    pub struct ShipProgram {
        name: String,
    }

    impl ShipProgram {
        pub fn name(&self) -> &str {
            &self.name
        }
    }

    #[pymethods]
    impl ShipProgram {
        #[pyo3(signature = (*args))]
        fn __call__(&self, args: Vec<String>) -> PyResult<ShipRunnable> {
            Ok(ShipRunnable(Arc::new(Runnable::Command {
                prog: self.clone(),
                args,
            })))
        }
    }

    #[pyclass(frozen)]
    #[derive(Clone)]
    pub struct ShipRunnable(Arc<Runnable>);

    #[allow(dead_code)]
    #[derive(Clone)]
    enum Runnable {
        Command {
            prog: ShipProgram,
            args: Vec<String>,
        },
        Pipeline {
            predecessors: Vec<ShipRunnable>,
            final_cmd: ShipRunnable,
        },
        Subshell {
            runnable: ShipRunnable,
        },
    }

    #[pyclass]
    #[derive(Clone)]
    pub struct ShipResult {
        #[pyo3(get)]
        pub exit_code: u8,
    }

    impl From<&ShipRunnable> for shell_env::CommandSpec {
        fn from(runnable: &ShipRunnable) -> Self {
            match runnable.0.as_ref() {
                Runnable::Command { prog, args } => shell_env::CommandSpec::Command {
                    program: prog.name().to_string(),
                    args: args.clone(),
                },
                Runnable::Pipeline {
                    predecessors,
                    final_cmd,
                } => shell_env::CommandSpec::Pipeline {
                    predecessors: predecessors.iter().map(|p| p.into()).collect(),
                    final_cmd: Box::new(final_cmd.into()),
                },
                Runnable::Subshell { runnable } => shell_env::CommandSpec::Subshell {
                    runnable: Box::new(runnable.into()),
                },
            }
        }
    }

    #[pymethods]
    impl ShipRunnable {
        fn __or__(&self, other: &ShipRunnable) -> PyResult<ShipRunnable> {
            use Runnable::*;

            let result_inner = match (self.0.as_ref(), other.0.as_ref()) {
                // Atomic | Atomic -> Pipeline([lhs], rhs)
                // (Command and Subshell are both atomic units)
                (Command { .. } | Subshell { .. }, Command { .. } | Subshell { .. }) => {
                    Arc::new(Pipeline {
                        predecessors: vec![self.clone()],
                        final_cmd: other.clone(),
                    })
                }

                // Pipeline | Atomic -> extend pipeline
                (
                    Pipeline {
                        predecessors,
                        final_cmd,
                    },
                    Command { .. } | Subshell { .. },
                ) => {
                    let mut new_predecessors = predecessors.clone();
                    new_predecessors.push(final_cmd.clone());
                    Arc::new(Pipeline {
                        predecessors: new_predecessors,
                        final_cmd: other.clone(),
                    })
                }

                // Atomic | Pipeline -> prepend to pipeline
                (
                    Command { .. } | Subshell { .. },
                    Pipeline {
                        predecessors,
                        final_cmd,
                    },
                ) => {
                    let mut new_predecessors = vec![self.clone()];
                    new_predecessors.extend(predecessors.clone());
                    Arc::new(Pipeline {
                        predecessors: new_predecessors,
                        final_cmd: final_cmd.clone(),
                    })
                }

                // Pipeline | Pipeline -> flatten both
                (
                    Pipeline {
                        predecessors: lhs_preds,
                        final_cmd: lhs_final,
                    },
                    Pipeline {
                        predecessors: rhs_preds,
                        final_cmd: rhs_final,
                    },
                ) => {
                    let mut new_predecessors = lhs_preds.clone();
                    new_predecessors.push(lhs_final.clone());
                    new_predecessors.extend(rhs_preds.clone());
                    Arc::new(Pipeline {
                        predecessors: new_predecessors,
                        final_cmd: rhs_final.clone(),
                    })
                }
            };

            Ok(ShipRunnable(result_inner))
        }

        fn __call__(&self) -> PyResult<ShipResult> {
            let spec: shell_env::CommandSpec = self.into();
            let result = shell_env::execute(&spec);
            Ok(ShipResult {
                exit_code: result.exit_code,
            })
        }
    }

    #[pyfunction]
    #[pyo3(signature = (name))]
    fn prog(name: String) -> PyResult<ShipProgram> {
        // TODO: Resolve the program from the shell environment
        Ok(ShipProgram { name })
    }

    #[pyfunction]
    #[pyo3(signature = (prog, *args))]
    fn cmd(prog: ShipProgram, args: Vec<String>) -> PyResult<ShipRunnable> {
        // PyO3 automatically converts:
        // - cmd to String (calls __str__ if needed)
        // - each arg to String (calls __str__ if needed)
        Ok(ShipRunnable(Arc::new(Runnable::Command { prog, args })))
    }

    #[pyfunction]
    #[pyo3(signature = (cmd1, cmd2, *cmds))]
    fn pipe(
        cmd1: ShipRunnable,
        cmd2: ShipRunnable,
        cmds: Vec<ShipRunnable>,
    ) -> PyResult<ShipRunnable> {
        let mut result = cmd1.__or__(&cmd2)?;
        for cmd in cmds {
            result = result.__or__(&cmd)?;
        }

        Ok(result)
    }

    #[pyfunction]
    fn sub(runnable: ShipRunnable) -> PyResult<ShipRunnable> {
        Ok(ShipRunnable(Arc::new(Runnable::Subshell { runnable })))
    }

    #[pyfunction]
    fn shexec(runnable: &ShipRunnable) -> PyResult<ShipResult> {
        runnable.__call__()
    }
}
