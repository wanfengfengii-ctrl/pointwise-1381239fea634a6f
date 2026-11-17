from collections.abc import Mapping
from labpipe.catalog.schemas import get_schema, is_valid_scalar


class BatchError(ValueError):
    pass


def validate_batch_id(batch_id):
    if type(batch_id) is not str or not batch_id.strip():
        raise BatchError('batch_id must be a nonempty string')
    return batch_id


def validate_row(row, position=0):
    if not isinstance(row, Mapping) or set(row) != {'row_id', 'station', 'value'}:
        raise BatchError(f'row {position}: expected row_id, station, value')
    for field in ('row_id', 'station'):
        if type(row[field]) is not str or not row[field].strip():
            raise BatchError(f'row {position}: invalid {field}')
    value = row['value']
    if not is_valid_scalar(value) or type(value) is str:
        raise BatchError(f'row {position}: value must be integer or null')
    return dict(row)


def validate_batch(table, batch_id, rows):
    try:
        get_schema(table)
    except ValueError as exc:
        raise BatchError(str(exc)) from exc
    validate_batch_id(batch_id)
    normalized = []
    seen = set()
    try:
        iterator = iter(rows)
    except TypeError as exc:
        raise BatchError('rows must be iterable') from exc
    for position, row in enumerate(iterator):
        item = validate_row(row, position)
        if item['row_id'] in seen:
            raise BatchError(f'row {position}: duplicate row_id {item["row_id"]}')
        seen.add(item['row_id'])
        normalized.append(item)
    return normalized
