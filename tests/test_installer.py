from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = (ROOT / "packaging" / "installer" / "TokenMeter.iss").read_text(encoding="utf-8")


def test_installer_has_stable_per_user_identity_and_paths():
    assert "AppId={{6CF354B5-80AE-48BF-AFC5-890BDA5D8862}" in SCRIPT
    assert "DefaultDirName={localappdata}\\Programs\\TokenMeter" in SCRIPT
    assert "PrivilegesRequired=lowest" in SCRIPT
    assert "UsePreviousAppDir=yes" in SCRIPT
    assert 'Filename: "{app}\\{#MyAppExeName}"' in SCRIPT
    assert 'MessagesFile: "languages\\ChineseSimplified.isl"' in SCRIPT
    assert (
        ROOT / "packaging" / "installer" / "languages" / "ChineseSimplified.isl"
    ).is_file()


def test_installer_uses_tokenmeter_brand_icon():
    assert "SetupIconFile=..\\..\\assets\\TokenMeter.ico" in SCRIPT
    assert (ROOT / "assets" / "TokenMeter.ico").is_file()


def test_installer_creates_both_shortcuts_and_preserves_data():
    assert "{userdesktop}\\TokenMeter" in SCRIPT
    assert "{group}\\TokenMeter" in SCRIPT
    assert 'Name: "{app}\\data"' not in SCRIPT
    assert 'Excludes: "\\data\\*,\\pet\\*,\\_internal\\pet\\*,\\_internal\\assets\\pets\\*"' in SCRIPT
    assert 'Type: files; Name: "{userstartup}\\TokenMeter.lnk"' in SCRIPT


def test_installer_packages_the_complete_onedir_tree():
    assert 'Source: "..\\..\\dist\\TokenMeter\\*"' in SCRIPT
    assert "recursesubdirs" in SCRIPT
    assert "createallsubdirs" in SCRIPT


def test_silent_update_restarts_fixed_executable_without_touching_data():
    assert "/TOKENMETERUPDATE" in SCRIPT
    assert "Check: IsUpdateMode" in SCRIPT
    assert "Check: not IsUpdateMode" in SCRIPT
    assert 'Filename: "{app}\\{#MyAppExeName}"' in SCRIPT
