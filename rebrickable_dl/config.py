from io import IOBase
from pathlib import Path
from typing import Self

from pydantic import BaseModel
from pydantic_yaml import parse_yaml_file_as


class FileModel(BaseModel):
    @classmethod
    def from_file(cls, file: Path | str | IOBase) -> Self:
        return parse_yaml_file_as(cls, file)


class Config(FileModel):
    base_dir: Path
    api_key: str
