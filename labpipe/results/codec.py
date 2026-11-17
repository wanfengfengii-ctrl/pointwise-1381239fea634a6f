import json


class ResultError(ValueError):
    pass


def _validate_row(row):
    if type(row) is not dict or not row:
        raise ResultError('result row must be a nonempty object')
    for key, value in row.items():
        if type(key) is not str or not key.strip():
            raise ResultError('invalid result column')
        if value is not None and type(value) not in (int, str):
            raise ResultError('result values must be integer, string or null')
    return row


def encode_row(row):
    return json.dumps(_validate_row(row), ensure_ascii=False, separators=(',', ':'))


def decode_row(encoded):
    try:
        return _validate_row(json.loads(encoded))
    except (TypeError, ValueError) as exc:
        raise ResultError('invalid saved result row: ' + str(exc)) from exc


def encode_export_document(document):
    required = {'result_id', 'plan_id', 'snapshot_id', 'row_order', 'rows', 'provenance'}
    if type(document) is not dict or set(document) != required:
        raise ResultError('incomplete export document')
    if any(type(document[key]) is not int for key in ('result_id', 'plan_id', 'snapshot_id')):
        raise ResultError('invalid export identities')
    if type(document['rows']) is not list:
        raise ResultError('invalid export rows')
    for row in document['rows']:
        _validate_row(row)
    if document['row_order'] != list(range(len(document['rows']))):
        raise ResultError('invalid saved result order')
    if type(document['provenance']) is not dict:
        raise ResultError('missing export provenance')
    return json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
