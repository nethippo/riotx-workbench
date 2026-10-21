"""File input inspection: polling only stats; copy/read once per metadata version."""
import csv
import gzip
import hashlib
import json
import shutil
import stat
import uuid
from pathlib import Path

LIMIT = 100 * 1024 * 1024


def metadata(path):
    s = path.lstat()
    if not stat.S_ISREG(s.st_mode):
        raise ValueError('일반 파일만 입력으로 사용할 수 있습니다')
    if s.st_size > LIMIT:
        raise ValueError('파일 한도는 100MB입니다')
    return {'device': s.st_dev, 'inode': s.st_ino, 'size': s.st_size,
            'mtime_ns': s.st_mtime_ns, 'ctime_ns': s.st_ctime_ns}


def fingerprint(file_id, info):
    return hashlib.sha256(json.dumps([file_id, info], sort_keys=True).encode()).hexdigest()


def parser_key(options):
    return json.dumps(options, sort_keys=True)


def count_records(path, options, name):
    kind = options.get('type') or Path(name).suffix.lstrip('.').lower()
    if kind not in {'csv', 'tsv', 'psv', 'json', 'jsonl'}:
        return None, '이 형식은 입력 레코드 집계를 지원하지 않습니다'
    if options.get('conf') or options.get('header-line') is not None or options.get('cont'):
        return None, '사용자 지정 파일 파서 옵션: 입력 레코드 수 확인 불가'
    opener = gzip.open if options.get('gzip') else open
    with opener(path, 'rt', encoding=options.get('encoding') or 'utf-8-sig', newline='') as f:
        if kind == 'json':
            obj = json.load(f)
            return (len(obj) if isinstance(obj, list) else 1), None
        if kind == 'jsonl':
            count = 0
            for line in f:
                if line.strip():
                    json.loads(line)
                    count += 1
            return count, None
        for _ in range(int(options.get('skip-lines') or 0)):
            next(f, None)
        reader = csv.reader(f, delimiter=options.get('delimiter') or {'csv': ',', 'tsv': '\t', 'psv': '|'}[kind],
                            quotechar=options.get('quote') or '"')
        if options.get('header'):
            next(reader, None)
        return sum(1 for row in reader if row), None


def inspect_file(path, info, options, name):
    """Immutable copy prevents subsequent replacements from changing a queued run."""
    directory = path.parent / 'versions'
    directory.mkdir(exist_ok=True)
    target = directory / (uuid.uuid4().hex + Path(name).suffix)
    try:
        if metadata(path) != info:
            raise ValueError('파일 변경 중: 다음 조회에서 다시 확인합니다')
        shutil.copyfile(path, target)
        if metadata(path) != info:
            raise ValueError('파일 변경 중: 다음 조회에서 다시 확인합니다')
        try:
            count, note = count_records(target, options, name)
        except (ValueError, UnicodeError, LookupError, csv.Error, OSError, EOFError, RecursionError):
            count, note = None, '입력 레코드를 집계할 수 없습니다. 형식·인코딩·파서 설정을 확인하세요'
        return {'snapshot_file': str(target.relative_to(path.parent)), 'records': count, 'count_note': note}
    except BaseException:
        target.unlink(missing_ok=True)
        raise
