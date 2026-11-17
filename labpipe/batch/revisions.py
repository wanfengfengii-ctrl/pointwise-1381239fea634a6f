import json
from labpipe.batch.validation import BatchError, validate_batch
from labpipe.catalog.schemas import get_schema


class RevisionStore:
    def __init__(self, storage):
        self.storage = storage

    def current_revision(self, table):
        get_schema(table)
        row = self.storage.connection.execute(
            'SELECT revision_id FROM lp_current WHERE table_name=?', (table,)
        ).fetchone()
        return None if row is None else row[0]

    def get_revision(self, revision_id):
        row = self.storage.connection.execute(
            'SELECT id,table_name,rows_json FROM lp_revisions WHERE id=?', (revision_id,)
        ).fetchone()
        if row is None:
            raise BatchError('unknown revision: ' + str(revision_id))
        return {'revision_id': row['id'], 'table': row['table_name'],
                'rows': json.loads(row['rows_json'])}

    def read_revision(self, revision_id):
        return self.get_revision(revision_id)['rows']

    def get_receipt(self, table, batch_id):
        row = self.storage.connection.execute(
            'SELECT revision_id,rows_json FROM lp_batches WHERE table_name=? AND batch_id=?',
            (table, batch_id),
        ).fetchone()
        if row is None:
            raise BatchError('unknown accepted batch')
        return {'table': table, 'batch_id': batch_id, 'revision_id': row[0],
                'row_count': len(json.loads(row[1]))}

    def commit_batch(self, table, batch_id, rows):
        normalized = validate_batch(table, batch_id, rows)
        encoded = json.dumps(normalized, ensure_ascii=False, separators=(',', ':'))
        with self.storage.transaction() as db:
            previous = db.execute(
                'SELECT rows_json FROM lp_batches WHERE table_name=? AND batch_id=?',
                (table, batch_id),
            ).fetchone()
            if previous is not None and previous[0] == encoded:
                return self.get_receipt(table, batch_id)
            current = self.current_revision(table)
            merged = [] if current is None else self.read_revision(current)
            merged = [r for r in merged if r['batch_id'] != batch_id]
            merged.extend(dict(row, batch_id=batch_id) for row in normalized)
            merged.sort(key=lambda row: row['batch_id'])
            revision = db.execute(
                'INSERT INTO lp_revisions(table_name,rows_json) VALUES(?,?)',
                (table, json.dumps(merged, ensure_ascii=False)),
            ).lastrowid
            db.execute(
                'INSERT INTO lp_batches VALUES(?,?,?,?) ON CONFLICT(table_name,batch_id) '
                'DO UPDATE SET rows_json=excluded.rows_json,revision_id=excluded.revision_id',
                (table, batch_id, encoded, revision),
            )
            db.execute(
                'INSERT INTO lp_current VALUES(?,?) ON CONFLICT(table_name) '
                'DO UPDATE SET revision_id=excluded.revision_id', (table, revision),
            )
            return self.get_receipt(table, batch_id)
