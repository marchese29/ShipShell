use pyo3::prelude::*;
use std::sync::Arc;

#[pymodule]
pub mod shp {
    use super::*;

    #[pyclass(frozen)]
    #[derive(Clone)]
    pub struct ShellRunnable(Arc<ShellRunnableInner>);

    #[allow(dead_code)]
    #[derive(Clone)]
    enum ShellRunnableInner {
        Command {
            cmd: String,
            args: Vec<String>,
        },
        Pipeline {
            predecessors: Vec<ShellRunnable>,
            final_cmd: Box<ShellRunnable>,
        },
        Subshell(Box<ShellRunnable>),
    }

    #[pymethods]
    impl ShellRunnable {
        fn __or__(&self, other: &ShellRunnable) -> PyResult<ShellRunnable> {
            use ShellRunnableInner::*;

            let result_inner = match (self.0.as_ref(), other.0.as_ref()) {
                // Atomic | Atomic -> Pipeline([lhs], rhs)
                // (Command and Subshell are both atomic units)
                (Command { .. } | Subshell(_), Command { .. } | Subshell(_)) => {
                    Arc::new(Pipeline {
                        predecessors: vec![self.clone()],
                        final_cmd: Box::new(other.clone()),
                    })
                }

                // Pipeline | Atomic -> extend pipeline
                (
                    Pipeline {
                        predecessors,
                        final_cmd,
                    },
                    Command { .. } | Subshell(_),
                ) => {
                    let mut new_predecessors = predecessors.clone();
                    new_predecessors.push((**final_cmd).clone());
                    Arc::new(Pipeline {
                        predecessors: new_predecessors,
                        final_cmd: Box::new(other.clone()),
                    })
                }

                // Atomic | Pipeline -> prepend to pipeline
                (
                    Command { .. } | Subshell(_),
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
                    new_predecessors.push((**lhs_final).clone());
                    new_predecessors.extend(rhs_preds.clone());
                    Arc::new(Pipeline {
                        predecessors: new_predecessors,
                        final_cmd: rhs_final.clone(),
                    })
                }
            };

            Ok(ShellRunnable(result_inner))
        }
    }

    #[pyfunction]
    #[pyo3(signature = (cmd, *args))]
    fn cmd(cmd: String, args: Vec<String>) -> PyResult<ShellRunnable> {
        // PyO3 automatically converts:
        // - cmd to String (calls __str__ if needed)
        // - each arg to String (calls __str__ if needed)
        Ok(ShellRunnable(Arc::new(ShellRunnableInner::Command {
            cmd,
            args,
        })))
    }

    #[pyfunction]
    #[pyo3(signature = (cmd1, cmd2, *cmds))]
    fn pipe(
        cmd1: ShellRunnable,
        cmd2: ShellRunnable,
        cmds: Vec<ShellRunnable>,
    ) -> PyResult<ShellRunnable> {
        let mut result = cmd1.__or__(&cmd2)?;
        for cmd in cmds {
            result = result.__or__(&cmd)?;
        }

        Ok(result)
    }

    #[pyfunction]
    fn sub(runnable: ShellRunnable) -> PyResult<ShellRunnable> {
        Ok(ShellRunnable(Arc::new(ShellRunnableInner::Subshell(
            Box::new(runnable),
        ))))
    }
}
