import json
from labpipe.batch.revisions import RevisionStore
from labpipe.batch.validation import BatchError
from labpipe.catalog.schemas import get_schema, NORMALIZED_UNIT


class SnapshotError(ValueError):
    pass


def freeze(storage, observation_revision, calibration_revision):
    revisions = {'observations': observation_revision, 'calibrations': calibration_revision}
    bindings = {}
    with storage.transaction() as db:
        for table, revision in revisions.items():
            if type(revision) is not int:
                raise SnapshotError('revision ID must be an integer')
            try:
                source = RevisionStore(storage).get_revision(revision)
            except BatchError as exc:
                raise SnapshotError(str(exc)) from exc
            if source['table'] != table:
                raise SnapshotError('revision belongs to wrong table: ' + table)
            bindings[table] = {'revision_id': revision, 'schema': get_schema(table),
                               'unit': NORMALIZED_UNIT}
        return db.execute(
            'INSERT INTO lp_snapshots(doc) VALUES(?)',
            (json.dumps({'tables': bindings}, ensure_ascii=False),),
        ).lastrowid


def get_snapshot(storage, snapshot_id):
    row = storage.connection.execute(
        'SELECT doc FROM lp_snapshots WHERE id=?', (snapshot_id,)
    ).fetchone()
    if row is None:
        raise SnapshotError('unknown snapshot: ' + str(snapshot_id))
    return dict(json.loads(row[0]), snapshot_id=snapshot_id)
