import logging
from typing import Any

import requests

from rebrickable_dl.utils import NETLOC_REBRICKABLE, ThemeInfo

LOGGER = logging.getLogger(__name__)


class RbClient:
    url_base = f"https://{NETLOC_REBRICKABLE}"

    def __init__(self, key: str) -> None:
        self._key = key

    def get(
        self,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if params is None:
            params = {}
        params = {"key": self._key, **params}
        endpoint = self.url_base + path
        response = requests.get(endpoint, params)
        response.raise_for_status()
        response_json = response.json()
        assert isinstance(response_json, dict)
        return response_json

    def get_themes(
        self,
        page: int | None = None,
        page_size: int = 10000,
        ordering: str | None = None,
    ) -> dict[int, list[ThemeInfo]]:
        """
        https://rebrickable.com/api/v3/swagger/?key=#!/lego/lego_themes_list
        """
        params: dict[str, Any] = {}
        if page is not None:
            params["page"] = page
        if page_size is not None:
            params["page_size"] = page_size
        if ordering is not None:
            params["ordering"] = ordering

        LOGGER.info("Getting themes from API")
        response_json = self.get("/api/v3/lego/themes/", params)
        LOGGER.info("Got themes from API")

        results = response_json["results"]
        results.sort(key=lambda result: result["parent_id"] if result["parent_id"] is not None else -1)

        themes: dict[int, list[ThemeInfo]] = {}
        for result_json in results:
            result_obj = ThemeInfo.model_validate(result_json)
            theme_path = [result_obj]
            while theme_path[0].parent_id is not None:
                theme_path = themes[theme_path[0].parent_id] + theme_path
            themes[result_obj.id] = theme_path

        return themes

    def get_theme(
        self,
        theme_id: int,
    ) -> list[ThemeInfo]:
        """
        https://rebrickable.com/api/v3/swagger/?key=#!/lego/lego_themes_read
        """
        theme_path: list[ThemeInfo] = []
        while True:
            response_json = self.get(f"/api/v3/lego/themes/{theme_id}/")
            response_obj = ThemeInfo.model_validate(response_json)
            theme_path.insert(0, response_obj)
            theme_id = response_json["parent_id"]
            if not theme_id:
                break

        return theme_path
