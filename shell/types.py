from pathlib import Path
from typing import Annotated

from pydantic import BeforeValidator

ShellPath = Annotated[Path | str, BeforeValidator(lambda v: Path(v) if isinstance(v, str) else v)]
ShellInt = Annotated[str | int, BeforeValidator(lambda v: int(v) if isinstance(v, str) else v)]


class ShellError(Exception):
    def __init__(self, message: str, exit_code: int = 1) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
