from shell import py_env, user
from shell.environment import env
from shell.repl import run_repl


def main():
    # 1. Set up base shell environment (inherit from parent, set defaults)
    env.initialize()

    # 2. Phase 1 init: Load config.py (can customize PYSH_CONFIG_DIR)
    user.initialize_config()

    # 3. Bootstrap user venv with shell dependencies
    py_env.initialize_shell_venv()

    # 4. Phase 2 init: Load user.py (full shell functionality available)
    user.initialize_user()

    # 5. Start interactive session
    run_repl()


if __name__ == '__main__':
    main()
