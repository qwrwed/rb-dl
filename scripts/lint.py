import subprocess
import sys


def main() -> None:
    try:
        subprocess.run(["ruff", "format"], check=True)
        subprocess.run(["ruff", "check", "--fix"], check=False)
        subprocess.run(["mypy", "--strict", "."], check=True)
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
