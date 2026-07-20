"""Shared application identity and release metadata."""

from __future__ import annotations

APP_DISPLAY_NAME = "TokenMeter"
APP_STORAGE_NAME = "TokenSpider"
APP_VERSION = "1.10.4"

# Keep the legacy storage and mutex identities so upgrades retain user data,
# credentials, and single-instance coordination across every public rename.
SINGLE_INSTANCE_MUTEX = "Local\\TokenSpider.SingleInstance"

MAIN_EXECUTABLE_NAME = "TokenMeter.exe"
UPDATER_EXECUTABLE_NAME = "TokenMeterUpdater.exe"
SETUP_RELEASE_ASSET_TEMPLATE = "TokenMeter-Setup-v{version}-x64.exe"
SHA256_RELEASE_ASSET_NAME = "SHA256SUMS.txt"

GITHUB_REPOSITORY = "zensoku142/TokenMeter"
GITHUB_RELEASES_URL = f"https://github.com/{GITHUB_REPOSITORY}/releases"
GITHUB_RELEASES_API_URL = f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases"
GITHUB_LATEST_RELEASE_API_URL = f"{GITHUB_RELEASES_API_URL}/latest"
