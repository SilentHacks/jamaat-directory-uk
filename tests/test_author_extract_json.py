# tests/test_author_extract_json.py
from directory.ingest.author import extract_json


def test_extracts_bare_object():
    assert extract_json('{"a": 1}') == '{"a": 1}'


def test_extracts_from_fenced_block_with_prose():
    text = 'Sure!\n```json\n{"shape": "rules"}\n```\nHope that helps.'
    assert extract_json(text) == '{"shape": "rules"}'


def test_ignores_braces_inside_strings():
    text = 'noise {"label": "a}{b", "n": 2} trailing'
    assert extract_json(text) == '{"label": "a}{b", "n": 2}'


def test_returns_none_without_object():
    assert extract_json("no json here") is None
