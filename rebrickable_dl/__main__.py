import logging
from argparse import ArgumentParser, Namespace
from pathlib import Path

import browser_cookie3
from utils_python import setup_tqdm_logger

from rebrickable_dl.config import Config
from rebrickable_dl.rebrickable_dl import RebrickableDL
from rebrickable_dl.utils import NETLOC_REBRICKABLE


class ProgramArgsNamespace(Namespace):
    urls: list[str] | None
    config_path: Path
    file: Path | None


def get_args() -> ProgramArgsNamespace:
    parser = ArgumentParser()

    target_group = parser.add_mutually_exclusive_group()
    target_group.add_argument(
        "urls",
        metavar="URL",
        nargs="*",
    )
    target_group.add_argument(
        "-f",
        "--file",
        type=Path,
    )

    parser.add_argument(
        "-c",
        "--config-path",
        type=Path,
        default=Path(__file__).parent.parent / "config" / "app.yaml",
    )

    return parser.parse_args(namespace=ProgramArgsNamespace())


def main() -> None:
    setup_tqdm_logger(level=logging.INFO)
    args = get_args()
    config = Config.from_file(args.config_path)
    cookies = browser_cookie3.firefox(domain_name=NETLOC_REBRICKABLE)

    rbdl = RebrickableDL(
        base_dir=config.base_dir,
        api_key=config.api_key,
        cookies=cookies,
    )

    if args.urls:
        for url in args.urls:
            rbdl.download(url)
    elif args.file:
        urls = [
            url.strip() for url in args.file.read_text().strip().split("\n") if url and not url.strip().startswith("#")
        ]
        for url in urls:
            rbdl.download(url)
    else:
        while True:
            url = input("\nURL: ")
            rbdl.download(url)


if __name__ == "__main__":
    main()
