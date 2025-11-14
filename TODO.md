**For feature ideas that are not core shell functionalities**
- [ ] "ergo" module with more intuitive python bindings for common programs (like builtins and git)
- [ ] DEEP uv integration so people can use any python library they want directly from the shell
  - [ ] Standardized location for shell's python environment (like installed libraries)
- [ ] Translation layer from bash -> shp-python for easy compatibility
- [ ] Syntax highlighting (and customization?)
- [ ] Auto-Complete via LSP
- [ ] Support for writing command in $EDITOR

**Core features to get to (synchronous shell only)**
- [X] RC file
- [X] Run in scoped environment
  - [ ] Run arbitrary code in a scoped environment?
- [X] Collection of stdout and stderr on process result
  - [ ] Probably need to clean up this code
- [X] Ergonomic wrappers for the common unix programs (got the ones from the standard)
  - [X] Get others outside the standard (like which and echo)
- [X] Python-REPL integrations and custom prompts
- [X] More shell builtins
- [ ] Handling of traps
- [ ] Enable export of shell-internal environment variables (if the user wants to)