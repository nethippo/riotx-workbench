"""Translate owned messages at the HTTP boundary; stored data is unchanged."""
import json
import re
from pathlib import Path

CATALOG = json.loads(Path(__file__).with_name('translations.json').read_text())


def language(header):
    for item in (header or '').split(','):
        tag = item.split(';', 1)[0].strip().lower().split('-', 1)[0]
        if tag in {'en', 'ko'}:
            return tag
    return 'ko'


def translate(value, lang):
    if not isinstance(value, str):
        return value
    table=CATALOG if lang=='en' else {en:ko for ko,en in CATALOG.items()}
    if value in table:
        return table[value]
    if value.startswith('Value error, '):
        return 'Value error, ' + translate(value[len('Value error, '):], lang)
    prefix = '지원하지 않거나 관리되는 옵션:'
    prefix=prefix if lang=='en' else CATALOG[prefix]
    if value.startswith(prefix):
        return table[prefix] + value[len(prefix):]
    exception = re.fullmatch(r'([A-Za-z][A-Za-z0-9_.]*(?:Error|Exception): ?)(.*)', value)
    if exception:
        return exception[1] + translate(exception[2], lang)
    return value
