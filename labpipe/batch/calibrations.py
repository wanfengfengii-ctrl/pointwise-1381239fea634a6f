from labpipe.batch.validation import validate_batch
from labpipe.batch.revisions import RevisionStore


def accept_calibrations(storage, batch_id, rows):
    normalized = validate_batch('calibrations', batch_id, rows)
    receipt = RevisionStore(storage).commit_batch('calibrations', batch_id, normalized)
    if receipt['table'] != 'calibrations':
        raise RuntimeError('calibration receipt belongs to another source table')
    return receipt
