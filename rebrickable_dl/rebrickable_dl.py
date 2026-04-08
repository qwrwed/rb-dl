from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from http import HTTPStatus
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Literal, NoReturn
from urllib.parse import SplitResult, parse_qs, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from cloudscraper import CloudScraper
from lxml import etree
from requests import Response
from tqdm import tqdm

from rebrickable_dl.api import RbClient
from rebrickable_dl.moc_page import MocPage
from rebrickable_dl.set_page import SetPage, get_instructions_url
from rebrickable_dl.utils import (
    NETLOC_REBRICKABLE,
    get_links,
    is_url,
    normalize_url,
    split_url_path,
)

LOGGER = logging.getLogger(__name__)


@dataclass
class RebrickableDLConfig:
    """Configuration options for RebrickableDL."""

    use_tqdm: bool = True
    use_threads: bool = True
    set_paper_type: Literal["A4", "US Letter"] | None = "A4"


class RebrickableDL:
    def __init__(
        self,
        base_dir: Path,
        api_key: str,
        cookies: CookieJar | None = None,
        config: RebrickableDLConfig | None = None,
    ) -> None:
        if config is None:
            config = RebrickableDLConfig()

        self._base_dir = base_dir
        self._cookies = cookies
        self._config = config
        self._rb_client = RbClient(key=api_key)
        self.themes = self._rb_client.get_themes()

        self._cloud_scraper = CloudScraper.create_scraper()
        if self._cookies:
            self._cloud_scraper.cookies.update(self._cookies)

    def get_from_cloudflare_url(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        max_retries: int = 10,
        backoff_factor: float = 1.5,
    ) -> Response:
        if headers is None:
            headers = {}

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:139.0) Gecko/20100101 Firefox/139.0",
            **headers,
        }

        for attempt in range(max_retries):
            response = self._cloud_scraper.get(url, headers=headers)
            if response.status_code != HTTPStatus.TOO_MANY_REQUESTS:
                response.raise_for_status()
                assert isinstance(response, Response)
                return response
            # 429 Too Many Requests: exponential backoff
            wait_time = backoff_factor * (2**attempt)
            time.sleep(wait_time)
        # If we get here, all retries failed
        response.raise_for_status()
        raise RuntimeError("Failed to get a valid response and no exception was set.")

    def download_file_from_url(
        self,
        url: str,
        dest_path: Path,
        headers: dict[str, str] | None = None,
    ) -> int:
        response = self.get_from_cloudflare_url(url, headers)

        content_type = response.headers["content-type"]
        content_type_split = content_type.split(";", 1)
        mime_type = content_type_split[0].strip()

        if mime_type == "application/json":
            response_json = response.json()
            response_html = response_json.get("html")
            if not response_html:
                raise ValueError(f"Expected 'html' field in JSON response from '{url}'")
            soup = BeautifulSoup(response_html, "html.parser")
            download_button = soup.find("button", class_="js-post-button", attrs={"data-url": True})
            if not download_button:
                raise ValueError(f"Could not find download button in 'html' field of JSON response from '{url}'")
            orig_url_split = urlsplit(url)
            if not isinstance(download_button, Tag):
                raise NotImplementedError(f"No handling for type({download_button}=)!=bs4.Tag")
            data_url = download_button["data-url"]
            if isinstance(data_url, list):
                raise NotImplementedError(f"No handling for type({data_url=})==list")
            download_url_split = urlsplit(data_url)
            new_url = urlunsplit(
                SplitResult(
                    scheme=orig_url_split.scheme,
                    netloc=orig_url_split.netloc,
                    path=download_url_split.path,
                    query=download_url_split.query,
                    fragment=download_url_split.fragment,
                )
            )
            return self.download_file_from_url(new_url, dest_path, headers)

        # if content_type.startswith("text/html"):
        #     raise ValueError(f"Unexpected content_type '{content_type}' for '{url}' -> '{dest_path.name}', you may need to log in before you can download it")

        return dest_path.write_bytes(response.content)

    def get_inventory_bricklink_xml(self, inventory_id: str) -> str:
        url = f"https://{NETLOC_REBRICKABLE}/inventory/{inventory_id}/parts/?format=blxml&inc_spares=0"
        response = self.get_from_cloudflare_url(
            url,
            headers={"X-Requested-With": "XMLHttpRequest"},
        )

        data = response.json()
        status = data.get("status")
        if "html" in data and status == "success":
            html_content = data["html"]
            match = re.search(r"<textarea[^>]*>(.+?)</textarea>", html_content, re.DOTALL)
            if match:
                xml_content = match.group(1)
                return html.unescape(xml_content)
            raise ValueError(f"Could not find XML textarea in response from '{url}'")
        raise ValueError(f"Unexpected response from '{url}': {status=}, {data.keys()=}")

    def _download_parts_xml(
        self,
        page: SetPage | MocPage,
    ) -> None:
        inventory_id = page.get_inventory_id()
        parts_xml = self.get_inventory_bricklink_xml(inventory_id)

        x = etree.fromstring(parts_xml)
        pretty_xml = etree.tostring(x, pretty_print=True, encoding=str)

        assert isinstance(pretty_xml, str)

        parts_xml_path = self._base_dir / page.relative_dir_path / f"{page.id}_parts.xml"

        if parts_xml_path.is_file():
            LOGGER.info(f"Downloading updated parts to '{parts_xml_path}' (already exists)")
        else:
            LOGGER.info(f"Downloading parts list to '{parts_xml_path}'")

        parts_xml_path.write_text(pretty_xml, encoding="utf-8")

    def _download_images(
        self,
        page: SetPage | MocPage,
        image_links: dict[str, str],
    ) -> None:
        items = list(image_links.items())
        if self._config.use_tqdm:
            pbar = tqdm(total=len(items), leave=False, desc="Downloading images")
        else:
            pbar = None

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(
                    self._download_single_file,
                    page,
                    image_name,
                    image_link,
                    pbar,
                )
                for image_name, image_link in items
            ]
            for future in as_completed(futures):
                future.result()
        if pbar:
            pbar.close()

    def _download_moc_attachments_threaded(
        self,
        moc_page: MocPage,
    ) -> None:
        download_links = moc_page.get_download_links()
        items = list(download_links.items())
        if self._config.use_tqdm:
            pbar = tqdm(total=len(items), leave=False, desc="Downloading attachments")
        else:
            pbar = None

        with ThreadPoolExecutor(max_workers=8) as executor:
            futures = [
                executor.submit(
                    self._download_single_file,
                    moc_page,
                    download_title,
                    download_link,
                    pbar,
                )
                for download_title, download_link in items
            ]
            for future in as_completed(futures):
                future.result()
        if pbar:
            pbar.close()

    def _download_moc_attachments(
        self,
        moc_page: MocPage,
    ) -> None:
        download_links = moc_page.get_download_links()
        items = list(download_links.items())
        if self._config.use_tqdm:
            pbar = tqdm(total=len(items), leave=False, desc="Downloading attachments")
        else:
            pbar = None
        for download_title, download_link in download_links.items():
            self._download_single_file(
                moc_page,
                download_title,
                download_link,
                pbar,
            )
        if pbar:
            pbar.close()

    def _download_single_file(
        self,
        page: MocPage | SetPage,
        download_file_name: str,
        download_link: str,
        pbar: tqdm[NoReturn] | None = None,
    ) -> None:
        download_path = self._base_dir / page.relative_dir_path / download_file_name
        if download_path.exists():
            LOGGER.info(f"Skipping '{download_file_name}'; already exists at '{download_path}'")
        else:
            download_path.parent.mkdir(parents=True, exist_ok=True)
            LOGGER.info(f"Downloading '{download_file_name}' to '{download_path}'")
            self.download_file_from_url(download_link, download_path)
        if pbar:
            pbar.update(1)

    def download_moc(
        self,
        url: str,
    ) -> None:
        response = self.get_from_cloudflare_url(url)

        soup = BeautifulSoup(response.text, "lxml")
        # lxml parser: href argument must be a string, regex, or None. Use a custom filter after finding all candidates.
        a_candidates = soup.find_all("a", title="Find other MOCs in this theme")
        a = next(
            (
                a
                for a in a_candidates
                if a.has_attr("href") and isinstance(a["href"], str) and a["href"].startswith("/mocs/?theme=")
            ),
            None,
        )
        if not a:
            raise ValueError("Could not find theme link on MOC page")
        href = a["href"]
        assert isinstance(href, str)
        theme_id = int(parse_qs(urlsplit(href).query)["theme"][0])
        theme = self.themes[theme_id]
        theme_parts = [theme_part.name for theme_part in theme]
        moc_page = MocPage(
            url=urlunsplit(urlsplit(response.url)._replace(query="", fragment="")),
            response=response,
            theme_parts=theme_parts,
        )

        dir_path = self._base_dir / moc_page.relative_dir_path
        dir_path.mkdir(exist_ok=True, parents=True)

        photos_tab_response = self.get_from_cloudflare_url(f"https://{NETLOC_REBRICKABLE}/mocs/{moc_page.id}/photos/")
        image_links = moc_page.get_image_links(photos_tab_response)
        self._download_parts_xml(moc_page)
        self._download_images(
            moc_page,
            image_links,
        )
        if self._config.use_threads:
            self._download_moc_attachments_threaded(moc_page)
        else:
            self._download_moc_attachments(moc_page)
        moc_page.create_shortcut(dir_path)
        moc_page.create_building_instructions_link(dir_path)
        moc_page.update_purchase_marker(dir_path)

    def _download_set_instructions(
        self,
        set_page: SetPage,
    ) -> None:
        download_links = set_page.extract_set_instructions_links(paper_type=self._config.set_paper_type)
        items = list(download_links.items())
        if self._config.use_tqdm:
            pbar = tqdm(total=len(items), leave=False, desc="Downloading attachments")
        else:
            pbar = None
        for download_title, download_link in download_links.items():
            self._download_single_file(
                set_page,
                download_title,
                download_link,
            )
        if pbar:
            pbar.close()

    def download_set(
        self,
        url: str,
    ) -> None:
        set_response = self.get_from_cloudflare_url(url)
        instructions_url = get_instructions_url(url)
        instructions_response = self.get_from_cloudflare_url(instructions_url)

        soup = BeautifulSoup(set_response.text, "lxml")

        # Extract theme href
        theme_link = soup.find("td", string="Theme")
        theme_href = None
        if theme_link and isinstance(theme_link, Tag):
            theme_a = theme_link.find_next("a")
            if theme_a and isinstance(theme_a, Tag):
                theme_href = theme_a.get("href")
                LOGGER.info(f"Theme href: {theme_href}")
        if not theme_href:
            raise ValueError("Could not find thme link on MOC page")
        if isinstance(theme_href, list):
            raise TypeError(f"Expected single theme href, got list: {theme_href=!r}")

        urlsplit_theme_href = urlsplit(theme_href)

        theme_id = None
        theme_slug = None
        if "theme" in (theme_query := parse_qs(urlsplit_theme_href.query)):
            theme_id = int(theme_query["theme"][0])
        else:
            theme_slug = urlsplit_theme_href.path.strip("/").split("/")[-1]
            for theme in self.themes.values():
                if theme[-1].name.lower().replace(" ", "-") == theme_slug:
                    theme_id = theme[-1].id
                    break
        if theme_id is None:
            raise ValueError(f"Could not determine theme ID from theme href '{theme_href}'")

        theme_parts = [theme_part.name for theme_part in self.themes[theme_id]]

        set_page = SetPage(
            url=urlunsplit(urlsplit(set_response.url)._replace(query="", fragment="")),
            set_response=set_response,
            instructions_response=instructions_response,
            theme_parts=theme_parts,
        )

        dir_path = self._base_dir / set_page.relative_dir_path
        dir_path.mkdir(exist_ok=True, parents=True)

        # self.download_set_images(set_page)
        image_links = set_page.get_image_links()
        self._download_images(set_page, image_links)
        self._download_parts_xml(set_page)
        self._download_set_instructions(set_page)
        set_page.create_shortcut(dir_path)

    def _extract_user_mocs_links(
        self,
        url_split: SplitResult,
        page_idx: int,
        page_size: int | None = None,
        theme_ids: list[str] | None = None,
    ) -> set[str]:
        query_dict: dict[str, str | int] = {"page": page_idx}
        if page_size is not None:
            query_dict["sets_page_size"] = page_size
        if theme_ids:
            query_dict["theme"] = ",".join([str(theme_id) for theme_id in theme_ids])
        query = urlencode(query_dict, safe=",")
        page_url = urlunsplit(url_split._replace(query=query))
        LOGGER.info(f"Requesting '{page_url}'")

        response = self.get_from_cloudflare_url(page_url)
        soup = BeautifulSoup(response.text, "html.parser")
        links = get_links(soup)
        links_processed: set[str] = set()
        for link in links:
            path = urlsplit(link).path
            if not path.startswith("/mocs/MOC-"):
                continue
            full_url = urlunsplit(url_split._replace(path=path))
            links_processed.add(full_url)
        return links_processed

    def get_user_mocs(
        self,
        *,
        url: str | None = None,
        username: str | None = None,
        theme_ids: int | list[int] | str | list[int | str] | None = None,
        page_size: int | None = 400,
    ) -> list[str]:
        if not (url is None) ^ (username is None):
            raise TypeError(f"Must provide exactly one of url and username (got {url=!r}, {username=!r})")

        theme_ids_list: list[str]

        if theme_ids is None:
            theme_ids_list = []
            if url is not None:
                query_theme = parse_qs(urlsplit(url).query).get("theme")
                if query_theme:
                    theme_ids_list = query_theme[0].split(",")
        elif not isinstance(theme_ids, list):
            theme_ids_list = [str(theme_ids)]
        else:
            theme_ids_list = [str(theme_id) for theme_id in theme_ids]

        if username:
            url = f"https://{NETLOC_REBRICKABLE}/users/{username}/mocs/"

        assert url is not None  # already validated above

        url_split = urlsplit(url)

        seen_links: set[str] = set()
        page_idx = 1
        while True:
            links_processed = self._extract_user_mocs_links(
                url_split,
                page_idx,
                page_size,
                theme_ids=theme_ids_list,
            )

            new_links = links_processed - seen_links
            if not new_links:
                break
            seen_links |= new_links
            page_idx += 1
        return sorted(list(seen_links))

    def download_user(
        self,
        url_or_username: str,
    ) -> None:
        url = None
        username = None
        if is_url(url_or_username):
            url = url_or_username
        else:
            username = url_or_username

        iterator: Iterable[str] = self.get_user_mocs(
            url=url,
            username=username,
        )
        if self._config.use_tqdm:
            iterator = tqdm(iterator, position=0)
        for url in iterator:
            if isinstance(iterator, tqdm):
                iterator.set_description(url)
            self.download_moc(url)

    def download(
        self,
        url: str,
    ) -> None:
        if is_url(url):
            url = normalize_url(url)
            url_path_split = split_url_path(url)
            match url_type := url_path_split[0]:
                case "users":
                    self.download_user(url)
                case "mocs":
                    self.download_moc(url)
                case "sets":
                    self.download_set(url)
                case _:
                    raise NotImplementedError(f"Unhandled URL type '{url_type}' (from '{url}')")
        else:
            # assume username was provided instead
            breakpoint()
            pass
            username = url
            self.download_user(username)
