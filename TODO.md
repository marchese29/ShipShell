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
- [ ] Support for PS1, PS2, and PS4
- [ ] Handling of traps
- [ ] Enable export of shell-internal environment variables (if the user wants to)