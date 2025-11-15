"""
This file is the creator's current init.py
"""

##########################
# GLOBAL NAMESPACE SETUP #
##########################

# For the Shell
import shp
from shp import *  # noqa: F403
from core import *  # noqa: F403
from shp import repl  # noqa: F401
from shp.builtins import *  # noqa: F403

# Other conveniences
from pathlib import Path


#####################
# Environment setup #
#####################

shp.env["EDITOR"] = "nvim"

# Homebrew is a tad more involved
# Until we support bash compatibility the file looks like this:
# export HOMEBREW_PREFIX="/opt/homebrew";
# export HOMEBREW_CELLAR="/opt/homebrew/Cellar";
# export HOMEBREW_REPOSITORY="/opt/homebrew";
# fpath[1,0]="/opt/homebrew/share/zsh/site-functions";
# eval "$(/usr/bin/env PATH_HELPER_ROOT="/opt/homebrew" /usr/libexec/path_helper -s)"
# [ -z "${MANPATH-}" ] || export MANPATH=":${MANPATH#:}";
# export INFOPATH="/opt/homebrew/share/info:${INFOPATH:-}";
shp.env["HOMEBREW_PREFIX"] = Path("/") / "opt" / "homebrew"
shp.env["HOMEBREW_CELLAR"] = Path("/") / "opt" / "homebrew" / "Cellar"
shp.env["HOMEBREW_REPOSITORY"] = Path("/") / "opt" / "homebrew"
shp.env["PATH"].insert(0, Path("/") / "opt" / "homebrew" / "bin")
shp.env["PATH"].insert(0, Path("/") / "opt" / "homebrew" / "sbin")

# Jetbrains toolbox
shp.env["PATH"].insert(
    0,
    Path("/")
    / "Users"
    / "daniel"
    / "Library"
    / "Application Support"
    / "JetBrains"
    / "Toolbox"
    / "scripts",
)

# Python 3.12
shp.env["PATH"].append(
    Path("/")
    / "Library"
    / "Frameworks"
    / "Python.framework"
    / "Versions"
    / "3.12"
    / "bin"
)

shp.env["PATH"].insert(0, Path.home() / ".cargo" / "bin")

# TODO: NVM with bash compatibility
