from pathlib import Path
from labpipe.results.reader import read
from labpipe.results.codec import encode_export_document


def build_export_document(storage, result_id):
    return read(storage, result_id)


def export(storage, result_id, path=None):
    encoded = encode_export_document(build_export_document(storage, result_id))
    if path is not None:
        Path(path).write_text(encoded + '\n', encoding='utf-8')
    return encoded
