"""Shared application identity and release metadata."""

from __future__ import annotations

APP_DISPLAY_NAME = "TokenSpider"
APP_STORAGE_NAME = "TokenSpider"
APP_VERSION = "1.9.1"

# Keep the storage/mutex prefix stable so users who tested the temporary
# TokenScope naming can still reuse the same local state after reverting.
SINGLE_INSTANCE_MUTEX = "Local\\TokenSpider.SingleInstance"

MAIN_EXECUTABLE_NAME = "TokenSpider.exe"
UPDATER_EXECUTABLE_NAME = "TokenSpiderUpdater.exe"
MAIN_RELEASE_ASSET_TEMPLATE = "TokenSpider-v{version}-windows-x64.exe"
LEGACY_MAIN_RELEASE_ASSET_TEMPLATE = "TokenScope-v{version}-windows-x64.exe"
UPDATER_RELEASE_ASSET_TEMPLATE = "TokenSpiderUpdater-v{version}-windows-x64.exe"
LEGACY_UPDATER_RELEASE_ASSET_TEMPLATE = "TokenScopeUpdater-v{version}-windows-x64.exe"
SHA256_RELEASE_ASSET_NAME = "SHA256SUMS.txt"

GITHUB_REPOSITORY = "zensoku142/TokenSpider"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases"
GITHUB_RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases"
GITHUB_LATEST_RELEASE_API_URL = f"{GITHUB_RELEASES_API_URL}/latest"
