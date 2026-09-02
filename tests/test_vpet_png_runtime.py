from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PNG_ANIMATION = ROOT / "third_party" / "VPet" / "VPet-Simulator.Core" / "Graph" / "PNGAnimation.cs"


def _source() -> str:
    return PNG_ANIMATION.read_text(encoding="utf-8")


def test_png_animation_orders_directory_frames_by_filename():
    source = _source()
    enumeration = source.split('p.GetFiles("*.png")', 1)[1].split(".ToArray();", 1)[0]

    assert ".OrderBy(file => file.Name, StringComparer.OrdinalIgnoreCase)" in enumeration
    assert ".ThenBy(file => file.Name, StringComparer.Ordinal)" in enumeration


def test_png_animation_cache_identity_changes_with_frame_metadata():
    source = _source()
    cache_assignment = next(
        line
        for line in source.splitlines()
        if "GraphCore.CachePath" in line and "GetSourceFingerprint" in line
    )
    fingerprint = source.split("private static string GetSourceFingerprint", 1)[1].split(
        "/// <summary>", 1
    )[0]

    assert "GetSourceFingerprint(path, paths)" in cache_assignment
    assert "frame.Refresh();" in fingerprint
    assert ".Append(frame.Name)" in fingerprint
    assert ".Append(frame.Length)" in fingerprint
    assert ".Append(frame.LastWriteTimeUtc.Ticks)" in fingerprint
    assert "SHA256.HashData" in fingerprint
