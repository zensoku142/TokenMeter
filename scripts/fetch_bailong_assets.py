"""Fetch the pinned upstream animation archive without committing it to Git."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "pet_host" / "bailong"
URL = ("https://github.com/jingh12312-rgb/bailong-desktop-pet/releases/download/"
       "v0.5.1/bailong-desktop-pet-v0.5.1-windows-x64-webm.zip")
SHA256 = "4b636bf1e92a82a798b01be09cbd658cbfc46c548dd1a38acee31368b9c78580"


def main() -> None:
    catalog = json.loads((SOURCE / "actions.json").read_text(encoding="utf-8"))
    items = [catalog["idle"], *catalog["additionalIdleAnimations"], *catalog["variants"],
             catalog["dragAnimation"], catalog["landingAnimation"], catalog["quotaAnimation"],
             *filter(None, catalog["workAnimations"]), *catalog["movementAnimations"]]
    names = {Path(item["playbackFile"]).name for item in items}
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "upstream.zip"
        if len(sys.argv) > 1:
            shutil.copyfile(sys.argv[1], archive)
        else:
            with urllib.request.urlopen(URL, timeout=120) as response, archive.open("wb") as output:
                shutil.copyfileobj(response, output)
        # Pin the published archive digest before extracting any third-party content.
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != SHA256:
            raise RuntimeError("Bailong archive checksum mismatch")
        with zipfile.ZipFile(archive) as source:
            entries = [item for item in source.infolist()
                       if item.filename.endswith(".webm") and Path(item.filename).name in names]
            if len(entries) != len(names) or {Path(item.filename).name for item in entries} != names:
                raise RuntimeError("Bailong archive does not contain the expected animations")
            target = SOURCE / "webm"
            target.mkdir(exist_ok=True)
            # Extract only known basenames, never archive paths.
            for item in entries:
                with source.open(item) as data, (target / Path(item.filename).name).open("wb") as output:
                    shutil.copyfileobj(data, output)
    print(f"Prepared {len(names)} verified Bailong animations")


if __name__ == "__main__":
    main()
