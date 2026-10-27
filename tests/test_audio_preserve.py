"""Weather extraction must not erase a complete audio corpus registry."""
import hashlib
import json
from pathlib import Path

import pytest

from phenomena import audio


def source(tmp_path):
    root = tmp_path / 'audio'; (root / 'se').mkdir(parents=True)
    raw = b'synthetic same-source cue archive'
    (root / 'se/se.acb').write_bytes(raw)
    (root / 'se/walk.ogg').write_bytes(b'walk')
    (root / 'se/wind.ogg').write_bytes(b'wind')
    prior = {'package': 'shared', 'archive': 'audio/se/se.acb', 'status': 'succeeded',
             'archiveSetRevision': 2, 'archiveSetCount': 1, 'archiveSetIndex': 0,
             'streams': [{'cue': 'walk', 'subsong': 1, 'ogg': 'audio/se/walk.ogg'},
                         {'cue': 'wind', 'subsong': 2, 'ogg': 'audio/se/wind.ogg'}]}
    untouched = {'package': 'voice', 'archive': 'audio/voice/voice.acb', 'streams': []}
    (root / 'loop.json').write_text(json.dumps({'packages': [prior, untouched]}))
    return root, raw, prior, untouched


def fresh(raw):
    return {'package': 'shared', 'archive': 'audio/se/se.acb', 'status': 'succeeded',
            'sourceSha256': hashlib.sha256(raw).hexdigest(),
            'streams': [{'cue': 'wind', 'subsong': 2, 'ogg': 'audio/se/wind.ogg'}]}


def test_weather_subset_preserves_other_cues_and_other_banks(tmp_path, monkeypatch):
    root, raw, prior, untouched = source(tmp_path)
    library = audio.Library(root, 'audio', preserve_existing=True)
    library.packages = [fresh(raw)]
    monkeypatch.setattr(audio, 'attach_sequences', lambda row, data: row.update(
        sequenceTest={'cues': [s['cue'] for s in row['streams']], 'source': hashlib.sha256(data).hexdigest()}))
    result = library.finish()
    assert result['packages'][1] == untouched
    combined = result['packages'][0]
    assert combined['streams'] == prior['streams']
    assert combined['archiveSetRevision'] == 2
    assert combined['sequenceTest']['cues'] == ['walk', 'wind']
    assert combined['sourceSha256'] == hashlib.sha256(raw).hexdigest()


def test_changed_archive_fails_before_overwriting_audio(tmp_path):
    root, raw, prior, untouched = source(tmp_path)
    library = audio.Library(root, 'audio', preserve_existing=True)
    previous = (root / 'loop.json').read_bytes()
    with pytest.raises(ValueError, match='identity changed'):
        library.add('shared', 'se', b'another release')
    assert (root / 'se/se.acb').read_bytes() == raw
    assert (root / 'loop.json').read_bytes() == previous


def test_missing_preserved_stream_requires_repair(tmp_path):
    root, raw, prior, untouched = source(tmp_path)
    library = audio.Library(root, 'audio', preserve_existing=True)
    library.packages = [fresh(raw)]
    (root / 'se/walk.ogg').unlink()
    with pytest.raises(ValueError, match='stream is missing'):
        library.finish()


def test_default_library_still_replaces_explicit_whole_output(tmp_path):
    root, raw, prior, untouched = source(tmp_path)
    library = audio.Library(root, 'audio')
    library.packages = [fresh(raw)]
    assert library.finish()['packages'] == [fresh(raw)]
