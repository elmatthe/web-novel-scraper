"""Windows replace contention must not discard a full discovery checkpoint."""
import json

import pytest

from webnovel_scraper import atomic_io


@pytest.mark.parametrize('winerror', [5, 32, 33])
def test_transient_windows_replace_retries_same_durable_payload(tmp_path, monkeypatch, winerror):
    path = tmp_path / 'manifest.json'
    path.write_text('{"old": true}')
    original = atomic_io.os.replace
    attempts = []
    def busy_then_replace(source, target):
        attempts.append(source)
        if len(attempts) < 3:
            assert json.loads(path.read_text()) == {'old': True}
            error = PermissionError('Windows file is temporarily in use')
            error.winerror = winerror
            raise error
        original(source, target)
    monkeypatch.setattr(atomic_io.os, 'replace', busy_then_replace)
    atomic_io.write_json_atomic(path, {'new': True})
    assert json.loads(path.read_text()) == {'new': True}
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert list(tmp_path.iterdir()) == [path]


def test_persistent_contention_is_bounded_and_preserves_previous_file(tmp_path, monkeypatch):
    path = tmp_path / 'manifest.json'
    path.write_text('previous')
    attempts = []
    def busy(*args):
        attempts.append(args)
        error = PermissionError('persistent contention')
        error.winerror = 32
        raise error
    monkeypatch.setattr(atomic_io.os, 'replace', busy)
    with pytest.raises(PermissionError):
        atomic_io.write_json_atomic(path, {'new': True})
    assert 1 < len(attempts) <= 5
    assert path.read_text() == 'previous' and list(tmp_path.iterdir()) == [path]
