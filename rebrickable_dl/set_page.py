import logging
import re
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path
from typing import Literal
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup
from utils_python import sanitize_filename_windows_style

from rebrickable_dl.utils import (
    NETLOC_REBRICKABLE,
    NETLOC_REBRICKABLE_CDN,
    create_shortcut,
    resolve_theme_parts,
    split_url_path,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class SetPage:
    url: str
    set_response: requests.Response
    instructions_response: requests.Response
    theme_parts: list[str]

    @cached_property
    def theme_name(self) -> str:
        return " - ".join([theme_part for theme_part in self.theme_parts])

    @property
    def page_type(self) -> str:
        return "Sets"

    @cached_property
    def _url_path_split(self) -> list[str]:
        return urlsplit(self.url).path.strip("/").split("/")

    @property
    def id(self) -> str:
        return self._url_path_split[1]

    @property
    def slug(self) -> str:
        return self._url_path_split[2]

    @property
    def relative_dir_path(self) -> Path:
        dir_name = sanitize_filename_windows_style("_".join([self.id, self.slug])).replace(" ", "-")
        theme_path = [sanitize_filename_windows_style(part) for part in resolve_theme_parts(self.theme_parts)]
        dir_path = Path("_" + self.page_type, *theme_path, dir_name)
        return dir_path

    @cached_property
    def set_soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.set_response.text, "html.parser")

    @cached_property
    def instructions_soup(self) -> BeautifulSoup:
        return BeautifulSoup(self.instructions_response.text, "html.parser")

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
        Extract inventory ID from the set page.
        The inventory ID is embedded in form actions and other elements on the page.
        """

        # Search directly in the HTML text for /inventory/NNNN pattern
        # The inventory ID appears in form actions like: /inventory/294290/parts_file/slow/
        match = re.search(r"/inventory/(\d+)", self.set_response.text)
        if match is None:
            raise TypeError(f"Could not extract inventory ID from '{self.url}'")

        inventory_id = match.group(1)
        if not isinstance(inventory_id, str):
            raise TypeError(f"{inventory_id!r} is not {str}")

        LOGGER.debug(f"Found inventory ID: {inventory_id}")
        return inventory_id

    def get_image_links(self) -> dict[str, str]:
        image_links = {}
        imgs = self.set_soup.find_all("img")
        for img in imgs:
            source = img.get("data-src")
            if not source:
                source = img.get("src")
            if not source:
                continue
            if urlsplit(source).netloc != NETLOC_REBRICKABLE_CDN:
                continue

            path_parts = split_url_path(source)

            if "mocs" in path_parts:
                continue

            if path_parts[2] == "sets" and self.id.lower() not in source:
                continue

            if path_parts[1] == "thumbs":
                path_parts.pop()
                image_name = "_".join(path_parts[-2:])
            elif path_parts[1] in ("avatar", "stores"):
                continue
            elif path_parts[0] == "media":
                image_name = path_parts[-1]
            else:
                breakpoint()
                pass

            image_links[image_name] = source

        return image_links

    def extract_set_instructions_links(
        self,
        paper_type: Literal["A4", "US Letter"] | None = "A4",
    ) -> dict[str, str]:
        a_tags = list(self.instructions_soup.find_all("a", href=True))
        a_tags_filtered = []

        for a in a_tags:
            href = a["href"]
            if "/download/" not in href:
                continue

            text = a.get_text()
            if paper_type and f"Paper: {paper_type}" not in text:
                continue

            span = a.find("span", title=True)
            text = span.get_text(strip=True)
            counter_text = re.search(r"\b(\d+)/(\d+)\b", text)
            subtitle = ("_" + counter_text.group().replace("/", "_of_")) if counter_text else ""
            title = f"instructions_{self.id}_{self.slug}{subtitle}.pdf"

            # TODO: include V29 (A4) or V39 (US) in filename too

            link_full = urlunsplit(urlsplit(href)._replace(scheme="https", netloc=NETLOC_REBRICKABLE))
            a_tags_filtered.append((title, link_full))

        return dict(sorted(a_tags_filtered))

    # @cached_property
    # def theme(self):
    #     a = self.soup.find(
    #         "a", title="Find other MOCs in this theme", href=lambda h: h and h.startswith("/mocs/?theme=")
    #     )
    #     theme_id = int(parse_qs(urlsplit(a["href"]).query)["theme"][0])
    #     theme_info = self.rb_client.get_theme(theme_id)
    #     theme_name = " - ".join([theme["name"] for theme in theme_info])
    #     return theme_name


def get_instructions_url(set_url: str) -> str:
    print(f"Extracting instructions URL from set URL: {set_url}")
    set_id = split_url_path(set_url)[1]
    return urlunsplit(
        SplitResult(
            scheme="https",
            netloc=NETLOC_REBRICKABLE,
            path="/".join(["instructions", set_id, ""]),
            query="",
            fragment="",
        )
    )
