"""Preview Xiaolong in the existing pet framework with isolated demo settings."""

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


if __name__ == "__main__":
    from examples import vpet_preview
    from core import pet_extension
    from scripts.build_release import _pet_manifest

    build = ROOT / "build/vpet-bailong"
    if not (build / "TokenMeter.Pet.exe").is_file():
        raise SystemExit("请先构建：python scripts/build_vpet.py --output build/vpet-bailong --framework-dependent")
    manifest = _pet_manifest(build)
    manifest["installed_characters"] = sorted(manifest["character_resources"])
    (build / pet_extension.PACK_MANIFEST).write_text(json.dumps(manifest), encoding="utf-8")
    state = ROOT / "build/bailong-framework-preview-state"
    layout = state / "vpet/layout.json"
    layout.parent.mkdir(parents=True, exist_ok=True)
    value = json.loads(layout.read_text(encoding="utf-8")) if layout.is_file() else {}
    value["character"] = "bailong"
    layout.write_text(json.dumps(value), encoding="utf-8")
    # 复用原面板与宿主桥；扩展和偏好只指向构建目录，不安装或改写真实用户配置。
    with patch.object(pet_extension, "extension_directory", return_value=build), \
         patch.object(vpet_preview.config, "CONFIG_DIR", state):
        raise SystemExit(vpet_preview.main(preview_character="bailong"))
