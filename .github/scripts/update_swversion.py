"""Write the latest Hue Bridge software version into emulated_hue/definitions.json."""
import json
import os
import re
import sys
import urllib.request
from html import unescape
from pathlib import Path

RELEASE_NOTES_URL = "https://www.philips-hue.com/en-us/support/release-notes/bridge"
DEFINITIONS_FILE = Path(__file__).parents[2] / "emulated_hue" / "definitions.json"

# The notes headline each release "Bridge v2 Software version 6.3.1978545030";
# the swversion a bridge reports is the trailing ten digits.
VERSION_PATTERN = re.compile(r"software version\s+[\d.]*?(\d{10})\b", re.IGNORECASE)


def get_latest_version() -> str:
    """Return the newest swversion listed on the Hue release notes page."""
    request = urllib.request.Request(  # noqa: S310 - fixed https URL
        RELEASE_NOTES_URL, headers={"User-Agent": "Mozilla/5.0"}
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
        page = response.read().decode("utf-8", errors="replace")
    text = unescape(re.sub(r"<[^>]+>", " ", page))
    if not (match := VERSION_PATTERN.search(text)):
        sys.exit("No software version found on the release notes page")
    return match.group(1)


def main() -> None:
    """Update the bridge swversion if the release notes list a newer one."""
    latest = get_latest_version()
    definitions = DEFINITIONS_FILE.read_text(encoding="utf-8")
    current = json.loads(definitions)["bridge"]["basic"]["swversion"]
    print(f"Current version: {current}, latest version: {latest}")
    if latest != current:
        updated = definitions.replace(
            f'"swversion": "{current}"', f'"swversion": "{latest}"', 1
        )
        DEFINITIONS_FILE.write_text(updated, encoding="utf-8")
    if output_file := os.getenv("GITHUB_OUTPUT"):
        with open(output_file, "a", encoding="utf-8") as output:
            output.write(f"version={latest}\n")


if __name__ == "__main__":
    main()
