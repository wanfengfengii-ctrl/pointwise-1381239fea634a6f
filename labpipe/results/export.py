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


def build_audit_export_document(storage, result_id):
    from labpipe.results.reader import read_audit
    return read_audit(storage, result_id)


def export_audit(storage, result_id, path=None):
    from labpipe.results.audit_codec import encode_audit_document
    encoded = encode_audit_document(build_audit_export_document(storage, result_id))
    if path is not None:
        Path(path).write_text(encoded + '\n', encoding='utf-8')
    return encoded
