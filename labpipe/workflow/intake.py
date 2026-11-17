import json
from pathlib import Path
from labpipe.batch.observations import accept_observations
from labpipe.batch.calibrations import accept_calibrations
from labpipe.batch.validation import BatchError


def ingest(storage, table, batch_id, rows):
    if table == 'observations':
        return accept_observations(storage, batch_id, rows)
    if table == 'calibrations':
        return accept_calibrations(storage, batch_id, rows)
    raise BatchError('unknown source table: ' + str(table))


def ingest_sample(storage, sample_path):
    sample = json.loads(Path(sample_path).read_text(encoding='utf-8'))
    receipts = {}
    with storage.transaction():
        for table in ('observations', 'calibrations'):
            request = sample[table]
            receipts[table] = ingest(storage, table, request['batch_id'], request['rows'])
    return receipts
