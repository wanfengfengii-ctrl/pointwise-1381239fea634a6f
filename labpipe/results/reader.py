from dataclasses import asdict
from labpipe.results.codec import ResultError, decode_row
from labpipe.results.provenance import decode_provenance
from labpipe.results.audit_codec import decode_audit, validate_audit


def read_result_rows(storage, result_id):
    return read(storage, result_id)['rows']


def read(storage, result_id):
    with storage.transaction() as db:
        header = db.execute(
            'SELECT plan_id,provenance FROM lp_results WHERE id=?', (result_id,)
        ).fetchone()
        if header is None or header['provenance'] is None:
            raise ResultError('unknown or unpublished result: ' + str(result_id))
        provenance = decode_provenance(header['provenance'])
        saved = db.execute(
            'SELECT ordinal,row_json FROM lp_result_rows WHERE result_id=? ORDER BY ordinal',
            (result_id,),
        ).fetchall()
        order = [item['ordinal'] for item in saved]
        if order != list(range(provenance.row_count)) or header['plan_id'] != provenance.plan_id:
            raise ResultError('saved result is incomplete or inconsistent')
        return {'result_id': result_id, 'plan_id': provenance.plan_id,
                'snapshot_id': provenance.snapshot_id, 'row_order': order,
                'rows': [decode_row(item['row_json']) for item in saved],
                'provenance': asdict(provenance)}


def read_audit(storage, result_id):
    document = read(storage, result_id)
    with storage.transaction() as db:
        row = db.execute(
            'SELECT doc FROM lp_result_audit WHERE result_id=?', (result_id,)
        ).fetchone()
    if row is None:
        raise ResultError('result has no audited evidence: ' + str(result_id))
    audit = decode_audit(row['doc'])
    validate_audit(audit, document['provenance']['revisions'],
                   document['provenance']['row_count'])
    document['audit'] = audit
    return document
