from labpipe.catalog.snapshots import get_snapshot, SnapshotError
from labpipe.batch.revisions import RevisionStore


class SnapshotRelationReader:
    def __init__(self, storage, snapshot_id):
        self.storage = storage
        self.snapshot = get_snapshot(storage, snapshot_id)

    def schema(self, table):
        if table not in self.snapshot['tables']:
            raise SnapshotError('table absent from fixed input: ' + str(table))
        return dict(self.snapshot['tables'][table]['schema'])

    def scan(self, table):
        self.schema(table)
        revision = self.snapshot['tables'][table]['revision_id']
        source = RevisionStore(self.storage).get_revision(revision)
        if source['table'] != table:
            raise SnapshotError('fixed relation has inconsistent table ownership')
        for row in source['rows']:
            yield dict(row)
