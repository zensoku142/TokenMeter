"""Build one independently installable VPet character resource pack."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile
from pathlib import Path

try:
    from scripts.build_vpet_rigged_pet import render_catalog
    from scripts.vpet_action_contract import load_contract, validate_contract
except ModuleNotFoundError:
    from build_vpet_rigged_pet import render_catalog
    from vpet_action_contract import load_contract, validate_contract


ROOT = Path(__file__).resolve().parents[1]
CHARACTERS = ROOT / "pet_host" / "characters"
DEFAULT_OUTPUT = ROOT / "build" / "pet-characters"


def _manifest(source: Path) -> dict[str, object]:
    value = json.loads((source / "character.json").read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("character.json must contain an object")
    character_id = value.get("id")
    version = value.get("version")
    if not isinstance(character_id, str) or not character_id:
        raise ValueError("character.json id is required")
    if not isinstance(version, str) or not version:
        raise ValueError("character.json version is required")
    return value


def _write_zip(source: Path, destination: Path) -> None:
    # Frames are already PNG-compressed; storing them makes the downloadable ZIP byte-stable
    # across release runners instead of depending on the host zlib implementation.
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_STORED) as archive:
        for path in sorted(source.rglob("*"), key=lambda item: item.as_posix().casefold()):
            if not path.is_file():
                continue
            relative = path.relative_to(source).as_posix()
            info = zipfile.ZipInfo(relative, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_STORED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, path.read_bytes())


def build_character(character_id: str, output_dir: Path = DEFAULT_OUTPUT) -> Path:
    source = (CHARACTERS / character_id).resolve()
    if source.parent != CHARACTERS.resolve() or not source.is_dir():
        raise ValueError(f"unknown character: {character_id}")
    manifest = _manifest(source)
    if manifest["id"] != character_id:
        raise ValueError("character directory and manifest id differ")

    output_dir = output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"TokenMeter-Pet-Character-{character_id}-v{manifest['version']}.zip"
    destination = output_dir / filename
    with tempfile.TemporaryDirectory(prefix=f".{character_id}-", dir=output_dir) as temporary:
        stage = Path(temporary) / "payload"
        pet = stage / "resources" / "pet"
        pet.mkdir(parents=True)
        render_catalog(
            source / "source" / "layer-manifest.json",
            source / "source" / "motion-catalog.json",
            pet / "vup",
        )
        shutil.copy2(source / "vup.lps", pet / "vup.lps")
        shutil.copy2(source / "character.json", stage / "character.json")
        errors = validate_contract(
            load_contract(ROOT / "pet_host" / "vpet_action_contract.json"),
            pet / "vup",
            pet / "vup.lps",
        )
        if errors:
            raise ValueError("character action contract failed: " + "; ".join(errors))
        temporary_zip = Path(temporary) / filename
        _write_zip(stage, temporary_zip)
        temporary_zip.replace(destination)

    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    report = {
        "id": character_id,
        "version": manifest["version"],
        "archive": str(destination),
        "sha256": digest,
        "size": destination.stat().st_size,
    }
    (output_dir / f"{character_id}.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False))
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("character", nargs="?", default="blue-whale-maid")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    build_character(args.character, args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
