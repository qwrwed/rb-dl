import logging
import re
from dataclasses import dataclass
from enum import Enum
from functools import cached_property
from pathlib import Path
from urllib.parse import SplitResult, parse_qs, unquote, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup, Tag
from utils_python import sanitize_filename_windows_style

from rebrickable_dl.utils import (
    NETLOC_REBRICKABLE_CDN,
    create_shortcut,
    resolve_theme_parts,
    split_url_path,
)

LOGGER = logging.getLogger(__name__)


class PurchaseMarkerFileName(Enum):
    FREE = "free.pm.txt"
    UNPURCHASED = "unpurchased.pm.txt"
    PURCHASED = "purchased.pm.txt"


def strip_url(url: str) -> SplitResult:
    url_split = urlsplit(url)
    url_path = url_split.path.strip("/")
    return url_split._replace(
        fragment="",
        path=url_path,
    )


@dataclass
class MocPage:
    url: str
    response: requests.Response
    theme_parts: list[str]

    @cached_property
    def theme_name(self) -> str:
        return " - ".join([theme_part for theme_part in self.theme_parts])

    @property
    def page_type(self) -> str:
        return "Mocs"

    @cached_property
    def _url_path_split(self) -> list[str]:
        return urlsplit(self.url).path.strip("/").split("/")

    @property
    def id(self) -> str:
        return self._url_path_split[1]

    @property
    def author(self) -> str:
        return self._url_path_split[2]

    @property
    def slug(self) -> str:
        return self._url_path_split[3]

    @property
    def relative_dir_path(self) -> Path:
        author_unquoted = unquote(self.author)
        dir_name = sanitize_filename_windows_style("_".join([self.id, author_unquoted, self.slug])).replace(" ", "-")
        theme_path = [sanitize_filename_windows_style(part) for part in resolve_theme_parts(self.theme_parts)]
        dir_path = Path("_" + self.page_type, *theme_path, author_unquoted, dir_name)
        return dir_path

    @cached_property
    def soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.response.text, "html.parser")

    def create_shortcut(
        self,
        dir: Path,
        stem: str | None = None,
    ) -> None:
        if stem is None:
            stem = f"Shortcut - {self.id}"
        path = Path(dir, stem + ".url")
        create_shortcut(self.url, path)

    def get_inventory_id(self) -> str:
        """
        Extract inventory ID from the MOC page.
        The inventory ID is embedded in form actions and other elements on the page.
        """

        # Search directly in the HTML text for /inventory/NNNN pattern
        # The inventory ID appears in form actions like: /inventory/294290/parts_file/slow/
        match = re.search(r"/inventory/(\d+)", self.response.text)
        if match is None:
            raise TypeError(f"Could not extract inventory ID from '{self.url}'")

        inventory_id = match.group(1)
        if not isinstance(inventory_id, str):
            raise TypeError(f"{inventory_id!r} is not {str}")

        LOGGER.debug(f"Found inventory ID: {inventory_id}")
        return inventory_id

    def get_image_links(
        self,
        photos_tab_response: requests.Response,
    ) -> dict[str, str]:
        image_links = {}
        imgs = self.soup.find_all("img")
        for img in imgs:
            source = img.get("data-src")
            if not source:
                source = img.get("src")
            if not source:
                continue
            if urlsplit(source).netloc != NETLOC_REBRICKABLE_CDN:
                continue

            path_parts = split_url_path(source)

            if "sets" in path_parts:
                continue

            if path_parts[2] == "mocs" and self.id.lower() not in source:
                continue

            if path_parts[1] == "thumbs":
                path_parts.pop()
                image_name = "_".join(path_parts[-2:])
            elif path_parts[1] == "avatar":
                continue
            elif path_parts[0] == "media":
                image_name = path_parts[-1]
            else:
                breakpoint()
                pass

            image_links[image_name] = source

        photos_soup = BeautifulSoup(photos_tab_response.json()["html"], "html.parser")
        imgs = photos_soup.find_all("img")
        for img in imgs:
            source = img["data-src"]
            path_parts = split_url_path(source)
            path_parts.pop(path_parts.index("thumbs"))
            path_parts.pop()
            source_resolved = urlunsplit(
                SplitResult(
                    scheme="https",
                    netloc=NETLOC_REBRICKABLE_CDN,
                    path="/".join(path_parts),
                    query="",
                    fragment="",
                )
            )
            image_name = "_".join(path_parts[-2:])
            image_links[image_name] = source_resolved

        return image_links

    def get_download_links(self) -> dict[str, str]:
        url_split = urlsplit(self.url)
        download_links = {}
        for a in self.soup.find_all("a", href=True):
            if "/mocs/purchases/" in a["href"] and a.find("span"):
                # free
                title = a.find("span")["title"]
                download_link = urlunsplit(url_split._replace(path=a["href"]))
            elif "/mocs/purchases" in a.get("data-url", "") and "title" in a.attrs:
                # premium
                title = a["title"]
                download_link = urlunsplit(url_split._replace(path=a["data-url"], fragment=""))
            else:
                continue
            download_links[title] = download_link

        return download_links

    @cached_property
    def is_premium(self) -> bool:
        return bool(self.soup.find("a", href="/help/buying-premium-mocs/"))

    def update_purchase_marker(
        self,
        dir_path: Path,
    ) -> None:
        for pm in PurchaseMarkerFileName:
            (dir_path / pm.value).unlink(missing_ok=True)
        if not self.is_premium:
            (dir_path / PurchaseMarkerFileName.FREE.value).touch()
            return

        buy_button = self.soup.find("button", id="load_buy_moc_modal")

        if buy_button:
            (dir_path / PurchaseMarkerFileName.UNPURCHASED.value).touch()
        else:
            (dir_path / PurchaseMarkerFileName.PURCHASED.value).touch()

    def create_building_instructions_link(self, dir_path: Path) -> None:
        a = self.soup.find("a", href=lambda h: isinstance(h, str) and "/external/view/?" in h and "action=BI" in h)
        if a is None:
            return
        assert isinstance(a, Tag)

        href = a["href"]
        assert isinstance(href, str)

        url_split = urlsplit(href)
        external_url = unquote(parse_qs(url_split.query)["url"][0])
        stem = f"Shortcut - {self.id} - Instructions"
        path = Path(dir_path, stem + ".url")
        create_shortcut(external_url, path)

    # @cached_property
    # def theme(self):
    #     a = self.soup.find(
    #         "a", title="Find other MOCs in this theme", href=lambda h: h and h.startswith("/mocs/?theme=")
    #     )
    #     theme_id = int(parse_qs(urlsplit(a["href"]).query)["theme"][0])
    #     theme_info = self.rb_client.get_theme(theme_id)
    #     theme_name = " - ".join([theme["name"] for theme in theme_info])
    #     return theme_name
