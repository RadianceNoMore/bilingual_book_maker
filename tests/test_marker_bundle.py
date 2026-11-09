import json
from zipfile import ZIP_STORED, ZipFile

import pytest

from book_maker.marker_bundle import STATE_PATH, read_state, write_bundle


def test_bundle_round_trip_and_replacement(tmp_path):
    source = tmp_path / 'source.epub'
    output = tmp_path / 'output.epub'
    with ZipFile(source, 'w') as book:
        book.writestr('mimetype', 'application/epub+zip', compress_type=ZIP_STORED)
        book.writestr('chapter.xhtml', '<p>text</p>')
        book.writestr(STATE_PATH, json.dumps({'old': True}))
    state = {'catalogue': {'美琴': {'target': 'Mikoto'}}, 'progress': 12}
    write_bundle(source, output, state)
    assert read_state(output) == state
    with ZipFile(output) as book:
        assert book.namelist().count(STATE_PATH) == 1
        assert book.namelist()[0] == 'mimetype'
        assert book.read('chapter.xhtml') == b'<p>text</p>'


def test_failed_bundle_keeps_previous_output(tmp_path):
    output = tmp_path / 'output.epub'
    output.write_bytes(b'previous')
    with pytest.raises(Exception):
        write_bundle(tmp_path / 'missing.epub', output, {})
    assert output.read_bytes() == b'previous'
    assert not list(tmp_path.glob('*.tmp'))
