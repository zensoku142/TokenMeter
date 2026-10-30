"""Large login cookies remain inside Credential Manager with atomic replacement."""

import pytest

from config import credentials


@pytest.fixture
def vault(monkeypatch):
    values = {}
    monkeypatch.setattr(credentials, "_advapi32", object())
    monkeypatch.setattr(credentials, "_read_raw_target", lambda target: values.get(target, ""))

    def write(target, value):
        assert len(value.encode("utf-16-le")) <= 2560
        values[target] = value

    monkeypatch.setattr(credentials, "_write_raw_target", write)
    monkeypatch.setattr(credentials, "_delete_target", lambda target: values.pop(target, None))
    return values


@pytest.mark.parametrize("secret", ["session=" + "a" * 6600, "汉字🙂" * 1200])
def test_large_credentials_round_trip_without_exceeding_native_limits(vault, secret):
    credentials.write_credential("WORKBUDDY_COOKIE", secret)
    assert credentials.read_credential("WORKBUDDY_COOKIE") == secret
    assert vault["TokenMeter/WORKBUDDY_COOKIE"] != secret
    previous = dict(vault)
    credentials.write_credential("WORKBUDDY_COOKIE", secret)
    assert vault == previous


def test_failed_chunk_write_preserves_previous_complete_credentials(vault, monkeypatch):
    credentials.write_credential("WORKBUDDY_COOKIE", "old=" + "a" * 6000)
    before = dict(vault)
    writer = credentials._write_raw_target
    count = 0

    def fail(target, value):
        nonlocal count
        count += 1
        if count == 2:
            raise OSError("synthetic storage failure")
        writer(target, value)

    monkeypatch.setattr(credentials, "_write_raw_target", fail)
    with pytest.raises(OSError):
        credentials.write_credential("WORKBUDDY_COOKIE", "new=" + "b" * 7000)
    assert vault == before
    assert credentials.read_credential("WORKBUDDY_COOKIE") == "old=" + "a" * 6000


def test_missing_or_corrupt_parts_are_never_returned_as_a_partial_cookie(vault):
    credentials.write_credential("WORKBUDDY_COOKIE", "session=" + "a" * 6000)
    part = next(target for target in vault if "/chunks/" in target)
    before = vault[part]
    vault[part] = "corrupt"
    assert credentials.read_credential("WORKBUDDY_COOKIE") == ""
    vault[part] = before
    assert credentials.read_credential("WORKBUDDY_COOKIE")
    del vault[part]
    assert credentials.read_credential("WORKBUDDY_COOKIE") == ""


def test_replacement_and_removal_clean_up_all_parts_and_legacy_namespaces(vault):
    credentials.write_credential("WORKBUDDY_COOKIE", "session=" + "a" * 6000)
    old = set(vault) - {"TokenMeter/WORKBUDDY_COOKIE"}
    credentials.write_credential("WORKBUDDY_COOKIE", "short-secret")
    assert not old.intersection(vault)
    assert credentials.read_credential("WORKBUDDY_COOKIE") == "short-secret"
    credentials.write_credential("WORKBUDDY_COOKIE", "session=" + "b" * 6000)
    vault["TokenScope/WORKBUDDY_COOKIE"] = "legacy"
    credentials.write_credential("WORKBUDDY_COOKIE", "")
    assert vault == {}
