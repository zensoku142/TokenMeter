import os
from pathlib import Path

os.environ["APPDATA"] = str(Path.cwd() / ".test-appdata")

import pytest

from app_update import (
    _release_from_payload,
    compare_versions,
    format_bytes,
    normalize_version,
)


def test_semver_comparison_supports_prefix_and_prerelease():
    assert normalize_version("v1.1.9") == "1.1.9"
    assert normalize_version("1.2.0-beta.1") == "1.2.0-beta.1"
    assert compare_versions("1.2.0", "1.2.0") == 0
    assert compare_versions("1.1.9", "1.2.0") < 0
    assert compare_versions("1.2.0-beta.1", "1.2.0") < 0
    assert compare_versions("1.2.0", "1.2.0-beta.1") > 0


def test_release_asset_selection_prefers_tokenspider_and_requires_updater():
    release = _release_from_payload(
        {
            "tag_name": "v1.3.0",
            "published_at": "2026-07-06T07:00:00Z",
            "body": "Bug fixes",
            "prerelease": False,
            "assets": [
                {
                    "name": "TokenSpider-v1.3.0-windows-x64.exe",
                    "browser_download_url": "https://github.com/zensoku142/TokenSpider/releases/download/v1.3.0/TokenSpider-v1.3.0-windows-x64.exe",
                    "size": 10,
                },
                {
                    "name": "TokenScope-v1.3.0-windows-x64.exe",
                    "browser_download_url": "https://github.com/zensoku142/TokenSpider/releases/download/v1.3.0/TokenScope-v1.3.0-windows-x64.exe",
                    "size": 11,
                },
                {
                    "name": "TokenSpiderUpdater-v1.3.0-windows-x64.exe",
                    "browser_download_url": "https://github.com/zensoku142/TokenSpider/releases/download/v1.3.0/TokenSpiderUpdater-v1.3.0-windows-x64.exe",
                    "size": 5,
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": "https://github.com/zensoku142/TokenSpider/releases/download/v1.3.0/SHA256SUMS.txt",
                    "size": 2,
                },
            ],
        }
    )

    assert release.version == "1.3.0"
    assert release.app_asset.name == "TokenSpider-v1.3.0-windows-x64.exe"
    assert release.updater_asset.name == "TokenSpiderUpdater-v1.3.0-windows-x64.exe"
    assert release.checksum_asset.name == "SHA256SUMS.txt"


def test_release_asset_selection_accepts_legacy_tokenscope_names():
    release = _release_from_payload(
        {
            "tag_name": "v1.3.0",
            "published_at": "2026-07-06T07:00:00Z",
            "body": "Bug fixes",
            "prerelease": False,
            "assets": [
                {
                    "name": "TokenScope-v1.3.0-windows-x64.exe",
                    "browser_download_url": "https://github.com/zensoku142/TokenSpider/releases/download/v1.3.0/TokenScope-v1.3.0-windows-x64.exe",
                    "size": 11,
                },
                {
                    "name": "TokenScopeUpdater-v1.3.0-windows-x64.exe",
                    "browser_download_url": "https://github.com/zensoku142/TokenSpider/releases/download/v1.3.0/TokenScopeUpdater-v1.3.0-windows-x64.exe",
                    "size": 5,
                },
                {
                    "name": "SHA256SUMS.txt",
                    "browser_download_url": "https://github.com/zensoku142/TokenSpider/releases/download/v1.3.0/SHA256SUMS.txt",
                    "size": 2,
                },
            ],
        }
    )

    assert release.app_asset.name == "TokenScope-v1.3.0-windows-x64.exe"
    assert release.updater_asset.name == "TokenScopeUpdater-v1.3.0-windows-x64.exe"


def test_format_bytes_uses_human_readable_units():
    assert format_bytes(0) == "未知"
    assert format_bytes(512) == "512 B"
    assert format_bytes(1024 * 1024) == "1.0 MB"
