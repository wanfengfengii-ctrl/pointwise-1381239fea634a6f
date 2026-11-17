import json
import sqlite3
import tempfile
from pathlib import Path
from labpipe.catalog.storage import Storage
from labpipe.batch.revisions import RevisionStore
from labpipe.workflow.intake import ingest
from labpipe.planning.compiler import compile
from labpipe.planning.registry import PlanRegistry
from labpipe.workflow.processing import execute
from labpipe.results.reader import read
from labpipe.results.export import export
from labpipe.results.codec import ResultError
from tests.test_intake import BusinessCase, quality_query, calibrations, EXPECTED


class TestResults(BusinessCase):
    def test_publication_failure_atomic(self):
        snapshot = self.seed()
        plan = compile(self.storage, snapshot, quality_query())
        old_id = execute(self.storage, plan)
        old = read(self.storage, old_id)
        before = [self.count(table) for table in ('lp_results', 'lp_result_rows')]
        triggers = [
            "CREATE TRIGGER fail_publish BEFORE INSERT ON lp_result_rows WHEN NEW.ordinal=1 BEGIN SELECT RAISE(ABORT,'row storage failure'); END",
            "CREATE TRIGGER fail_publish BEFORE UPDATE OF provenance ON lp_results BEGIN SELECT RAISE(ABORT,'provenance storage failure'); END",
        ]
        for trigger in triggers:
            self.storage.connection.execute(trigger)
            try:
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'storage failure'):
                    execute(self.storage, plan)
                self.assertEqual([self.count(table) for table in ('lp_results', 'lp_result_rows')], before)
                self.assertEqual(read(self.storage, old_id), old)
                self.assertEqual(self.storage.connection.execute(
                    'SELECT count(*) FROM lp_results WHERE provenance IS NULL').fetchone()[0], 0)
            finally:
                self.storage.connection.execute('DROP TRIGGER fail_publish')
        new_id = execute(self.storage, plan)
        self.assertNotEqual(new_id, old_id)
        self.assertEqual(read(self.storage, new_id)['rows'], old['rows'])

    def test_reopen_fixed_plan_and_export(self):
        snapshot = self.seed()
        query = quality_query()
        plan = compile(self.storage, snapshot, query)
        old_id = execute(self.storage, plan)
        old = read(self.storage, old_id)
        fixed = PlanRegistry(self.storage).get_metadata(plan)
        query['columns']['sum']['value']['name'] = 'station'
        ingest(self.storage, 'calibrations', 'bench',
               [dict(row, value=30 if i == 0 else 50) for i, row in enumerate(calibrations())])
        with tempfile.TemporaryDirectory(prefix='labpipe-results-') as directory:
            path = Path(directory) / 'archive.sqlite'
            with Storage.open(path) as disk:
                self.storage.connection.backup(disk.connection)
            with Storage.open(path) as reopened:
                reopened.initialize()
                self.assertEqual(PlanRegistry(reopened).get_metadata(plan), fixed)
                new_result = read(reopened, execute(reopened, plan))
                self.assertEqual(new_result['rows'], EXPECTED)
                self.assertEqual(new_result['provenance']['revisions'], old['provenance']['revisions'])
                self.assertEqual(read(reopened, old_id), old)
                exported = json.loads(export(reopened, old_id, Path(directory) / 'result.json'))
                self.assertEqual(exported, old)
                self.assertEqual(json.loads((Path(directory) / 'result.json').read_text(encoding='utf-8')), old)
                # Saved results remain readable when a plan cannot be executed.
                reopened.connection.execute('UPDATE lp_plans SET doc=? WHERE id=?', ('{}', plan))
                self.assertEqual(read(reopened, old_id), old)
                self.assertEqual(json.loads(export(reopened, old_id)), old)
                with self.assertRaises(ResultError):
                    read(reopened, 999999)
            with Storage.open(path) as reopened:
                receipt = RevisionStore(reopened).get_receipt('calibrations', 'bench')
                self.assertEqual(RevisionStore(reopened).read_revision(receipt['revision_id'])[0]['value'], 30)
                self.assertEqual(read(reopened, old_id), old)

    def test_exact_export_and_read_isolation(self):
        snapshot = self.seed(cal=[
            {'row_id': 'a', 'station': 'A', 'value': 2 ** 70},
            {'row_id': 'b', 'station': 'A', 'value': 3},
            {'row_id': 'n', 'station': 'A', 'value': None}])
        result_id = execute(self.storage, compile(self.storage, snapshot, quality_query()))
        saved = read(self.storage, result_id)
        changed = read(self.storage, result_id)
        changed['rows'][0]['sum'] = -1
        changed['provenance']['revisions']['calibrations'] = -1
        exported = json.loads(export(self.storage, result_id))
        self.assertEqual(exported, saved)
        self.assertEqual(exported['rows'][0]['sum'], 1180591620717411303427)
        self.assertIs(type(exported['rows'][0]['sum']), int)

    def test_unknown_result(self):
        for operation in (read, export):
            with self.assertRaises(ResultError):
                operation(self.storage, 999)
        self.assertEqual(self.count('lp_results'), 0)
