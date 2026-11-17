import json
import sqlite3
import tempfile
from pathlib import Path
from labpipe.catalog.storage import Storage
from labpipe.batch.revisions import RevisionStore
from labpipe.workflow.intake import ingest, ingest_sample
from labpipe.workflow.processing import process_day, execute
from labpipe.planning.compiler import compile
from labpipe.planning.registry import PlanRegistry
from labpipe.planning.format import PlanError
from labpipe.execution.runner import run_plan, ExecutionError
from labpipe.execution.environments import RowEnvironment
from labpipe.catalog.relations import SnapshotRelationReader
from labpipe.results.reader import read
from labpipe.results.codec import encode_row, decode_row, ResultError
from tests.test_intake import BusinessCase, observations, calibrations, quality_query, EXPECTED


class TestBoundaries(BusinessCase):
    def test_environment_exception_exit(self):
        parent = RowEnvironment().extend(1, {'station': 'A'})
        child = None
        with self.assertRaisesRegex(OSError, 'evaluation'):
            with parent.child_scope() as child:
                inner = child.extend(1, {'station': 'B'})
                self.assertEqual(inner.resolve(1, 'station'), 'B')
                raise OSError('evaluation failed')
        self.assertEqual(parent.resolve(1, 'station'), 'A')
        with self.assertRaises(RuntimeError):
            child.resolve(1, 'station')

    def test_replay_and_reopen_initialization(self):
        with tempfile.TemporaryDirectory(prefix='labpipe-reopen-') as directory:
            path = Path(directory) / 'state.sqlite'
            with Storage.open(path) as storage:
                first = ingest(storage, 'observations', 'day', observations())
                ingest(storage, 'calibrations', 'bench', calibrations())
                changed = ingest(storage, 'observations', 'day', observations()[:1])
            with Storage.open(path) as storage:
                storage.initialize()
                self.assertEqual(ingest(storage, 'observations', 'day', observations()[:1]), changed)
                revisions = RevisionStore(storage)
                self.assertEqual(revisions.current_revision('observations'), changed['revision_id'])
                self.assertEqual(len(revisions.read_revision(first['revision_id'])), 3)
                self.assertEqual(len(revisions.read_revision(changed['revision_id'])), 1)
                storage.connection.execute('UPDATE lp_meta SET version=999')
            with self.assertRaisesRegex(sqlite3.DatabaseError, 'incompatible'):
                Storage.open(path)
            connection = sqlite3.connect(path)
            try:
                self.assertEqual(connection.execute('SELECT version FROM lp_meta').fetchone()[0], 999)
                self.assertEqual(connection.execute('SELECT count(*) FROM lp_revisions').fetchone()[0], 3)
            finally:
                connection.close()

    def test_codec_rejects_invalid_values(self):
        for value in (True, 1.5, [], {}):
            with self.assertRaises(ResultError):
                encode_row({'v': value})
        for encoded in ('{"v":true}', '{"v":1.5}', '[]', '{bad'):
            with self.assertRaises(ResultError):
                decode_row(encoded)
        self.assertEqual(decode_row(encode_row({'v': -(2 ** 100), 'text': '站点', 'n': None})),
                         {'v': -(2 ** 100), 'text': '站点', 'n': None})

    def test_plan_publication_storage_failure(self):
        snapshot = self.seed()
        self.storage.connection.execute(
            "CREATE TRIGGER fail_plan BEFORE INSERT ON lp_plans BEGIN SELECT RAISE(ABORT,'plan storage failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'plan storage failure'):
            compile(self.storage, snapshot, quality_query())
        self.assertEqual(self.count('lp_plans'), 0)

    def test_revision_publication_storage_failure(self):
        self.seed()
        previous = self.revisions.current_revision('observations')
        receipt = self.revisions.get_receipt('observations', 'day')
        count = self.count('lp_revisions')
        self.storage.connection.execute(
            "CREATE TRIGGER fail_source BEFORE UPDATE ON lp_current BEGIN SELECT RAISE(ABORT,'source storage failure'); END")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'source storage failure'):
            ingest(self.storage, 'observations', 'day', observations()[:1])
        self.assertEqual(self.revisions.current_revision('observations'), previous)
        self.assertEqual(self.revisions.get_receipt('observations', 'day'), receipt)
        self.assertEqual(self.count('lp_revisions'), count)

    def test_reader_input_mismatch_and_no_partial_execution(self):
        first = self.seed()
        plan = compile(self.storage, first, quality_query())
        second = self.seed(obs=[])
        with self.assertRaises(ExecutionError):
            run_plan(PlanRegistry(self.storage).load_plan(plan),
                     SnapshotRelationReader(self.storage, second))
        self.storage.connection.execute('UPDATE lp_plans SET doc=? WHERE id=?', ('{}', plan))
        with self.assertRaises(PlanError):
            execute(self.storage, plan)
        self.assertEqual(self.count('lp_results'), 0)

    def test_sample_process_day(self):
        with tempfile.TemporaryDirectory(prefix='labpipe-input-') as directory:
            path = Path(directory) / 'sample.json'
            path.write_text(json.dumps({
                'observations': {'batch_id': 'day', 'rows': observations()},
                'calibrations': {'batch_id': 'bench', 'rows': calibrations()},
            }), encoding='utf-8')
            receipts = ingest_sample(self.storage, path)
            self.assertEqual(ingest_sample(self.storage, path), receipts)
            ids = process_day(self.storage, receipts['observations']['revision_id'],
                              receipts['calibrations']['revision_id'], quality_query())
            self.assertEqual(read(self.storage, ids['result_id'])['rows'], EXPECTED)
