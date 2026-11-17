import sqlite3
from contextlib import contextmanager

STORAGE_VERSION = 1

_DDL = (
    'CREATE TABLE lp_meta(version INTEGER NOT NULL)',
    'CREATE TABLE lp_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL, rows_json TEXT NOT NULL)',
    'CREATE TABLE lp_current(table_name TEXT PRIMARY KEY, revision_id INTEGER NOT NULL REFERENCES lp_revisions(id))',
    'CREATE TABLE lp_batches(table_name TEXT NOT NULL, batch_id TEXT NOT NULL, rows_json TEXT NOT NULL, revision_id INTEGER NOT NULL REFERENCES lp_revisions(id), PRIMARY KEY(table_name,batch_id))',
    'CREATE TABLE lp_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT NOT NULL)',
    'CREATE TABLE lp_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT NOT NULL)',
    'CREATE TABLE lp_results(id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL REFERENCES lp_plans(id), provenance TEXT)',
    'CREATE TABLE lp_result_rows(result_id INTEGER NOT NULL REFERENCES lp_results(id), ordinal INTEGER NOT NULL, row_json TEXT NOT NULL, PRIMARY KEY(result_id,ordinal))',
)


class Storage:
    def __init__(self, connection):
        self.connection = connection
        self._savepoint = 0

    @classmethod
    def open(cls, path=':memory:'):
        connection = sqlite3.connect(str(path), isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        storage = cls(connection)
        try:
            storage.initialize()
        except BaseException:
            connection.close()
            raise
        return storage

    def initialize(self):
        with self.transaction():
            exists = self.connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='lp_meta'"
            ).fetchone()
            if exists:
                versions = self.connection.execute('SELECT version FROM lp_meta').fetchall()
                if len(versions) != 1 or versions[0][0] != STORAGE_VERSION:
                    raise sqlite3.DatabaseError('incompatible labpipe storage version')
                for name in ('lp_revisions', 'lp_current', 'lp_batches', 'lp_snapshots',
                             'lp_plans', 'lp_results', 'lp_result_rows'):
                    if not self.connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
                    ).fetchone():
                        raise sqlite3.DatabaseError('incomplete labpipe storage: ' + name)
                return
            for statement in _DDL:
                self.connection.execute(statement)
            self.connection.execute('INSERT INTO lp_meta VALUES(?)', (STORAGE_VERSION,))

    @contextmanager
    def transaction(self):
        self._savepoint += 1
        name = 'lp_tx_' + str(self._savepoint)
        self.connection.execute('SAVEPOINT ' + name)
        try:
            yield self.connection
            self.connection.execute('RELEASE ' + name)
        except BaseException:
            if self.connection.in_transaction:
                self.connection.execute('ROLLBACK TO ' + name)
                self.connection.execute('RELEASE ' + name)
            raise

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
