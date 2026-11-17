from labpipe.batch.validation import validate_batch
from labpipe.batch.revisions import RevisionStore


def accept_observations(storage, batch_id, rows):
    normalized = validate_batch('observations', batch_id, rows)
    receipt = RevisionStore(storage).commit_batch('observations', batch_id, normalized)
    if receipt['row_count'] != len(normalized):
        raise RuntimeError('observation acceptance lost a source identity')
    return receipt
