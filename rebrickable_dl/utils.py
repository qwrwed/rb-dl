import logging
from pathlib import Path
from urllib.parse import SplitResult, urlsplit

import validators
from bs4 import BeautifulSoup
from pydantic import BaseModel

try:
    from ctypes.wintypes import MAX_PATH as _WIN_MAX_PATH

    MAX_PATH = _WIN_MAX_PATH
except ValueError:  # raises on linux
    MAX_PATH = 4096  # see comments

LOGGER = logging.getLogger(__name__)


def create_shortcut(url: str, path: Path) -> None:
    Path(path).write_text(
        f"[InternetShortcut]\nURL={url}",
        newline="\r\n",
    )


def get_links(soup: BeautifulSoup) -> list[str]:
    return sorted(list({a["href"] for a in soup.find_all("a", href=True)}))


def is_url(
    url: str,
    require_schema: bool = True,
) -> bool:
    return bool(validators.url(url) or (validators.url("https://" + url) and not require_schema))


def normalize_url(url: str) -> str:
    url_split = urlsplit(url)
    if url_split.scheme:
        return url
    return "https://" + url


def split_url_path(url: str | SplitResult) -> list[str]:
    if isinstance(url, str):
        url = urlsplit(url)
    return url.path.strip("/").split("/")


NETLOC_REBRICKABLE = "rebrickable.com"
NETLOC_REBRICKABLE_CDN = "cdn.rebrickable.com"


class ThemeInfo(BaseModel):
    id: int
    parent_id: int | None
    name: str


class SetInfo(BaseModel): ...


def resolve_theme_parts(theme_parts: list[str]) -> list[str]:
    if len(theme_parts) == 1:
        theme_parts *= 2
    elif len(theme_parts) > 1:
        theme_parts = [theme_parts[0], " - ".join(theme_parts[1:])]
    return theme_parts
