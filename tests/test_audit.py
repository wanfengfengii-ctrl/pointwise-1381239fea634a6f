import copy
import json
import sqlite3
import tempfile
from pathlib import Path
from unittest.mock import patch
from labpipe.catalog.storage import Storage
from labpipe.planning.compiler import compile
from labpipe.workflow.processing import execute, execute_audited
from labpipe.results.reader import read, read_audit
from labpipe.results.export import export, export_audit
from labpipe.results.codec import ResultError
from labpipe.workflow.intake import ingest
from labpipe.catalog.snapshots import freeze
from tests.test_intake import (
    BusinessCase, quality_query, col, scan, eq, EXPECTED, observations, calibrations)
from tests.test_plans import nested_query
from tests.test_processing import bag


def call_at(output, path):
    calls = [c for c in output['calls'] if c['node'] == path]
    assert len(calls) == 1, (path, output)
    return calls[0]


class TestAudit(BusinessCase):
    def test_rows_match_ordinary_execution(self):
        snapshot = self.seed()
        queries = [quality_query(), nested_query(), nested_query('outer', 'inner'),
                   {'op': 'filter', 'input': quality_query(),
                    'where': eq(col('report', 'station'), {'op': 'lit', 'value': 'A'})}]
        for query in queries:
            with self.subTest(query=query):
                plan = compile(self.storage, snapshot, query)
                ordinary = execute(self.storage, plan)
                audited = execute_audited(self.storage, plan)
                self.assertNotEqual(ordinary, audited)
                self.assertEqual(read(self.storage, audited)['rows'],
                                 read(self.storage, ordinary)['rows'])
                document = read_audit(self.storage, audited)
                self.assertEqual(set(document), set(read(self.storage, audited)) | {'audit'})
                self.assertEqual(len(document['audit']), len(document['rows']))
                self.assertEqual(set(read(self.storage, audited)),
                                 {'result_id', 'plan_id', 'snapshot_id', 'row_order',
                                  'rows', 'provenance'})

    def test_projected_duplicates_keep_distinct_sources(self):
        snapshot = self.seed()
        report = quality_query()
        del report['columns']['row_id']
        document = read_audit(self.storage, execute_audited(
            self.storage, compile(self.storage, snapshot, report)))
        self.assertEqual(document['rows'][:2],
                         [{'station': 'A', 'count': 2, 'sum': 8}] * 2)
        self.assertEqual([o['source']['row_id'] for o in document['audit']],
                         ['o1', 'o2', 'o3'])
        first, second = document['audit'][0], document['audit'][1]
        self.assertEqual(first['source']['revision_id'],
                         document['provenance']['revisions']['observations'])
        for output in (first, second):
            count = call_at(output, ['columns', 'count'])
            total = call_at(output, ['columns', 'sum'])
            self.assertEqual([m['candidate'] for m in count['members']], [0, 1])
            self.assertEqual([m['candidate'] for m in total['members']], [0, 1])
            self.assertEqual([m['value'] for m in total['members']], [3, 5])
            self.assertEqual(total['result'], 8)
            self.assertEqual([c['source']['row_id']
                              for c in total['candidates']], ['c1', 'c2'])
        # Empty relation: candidates exist but none are accepted, result 0, no members.
        absence = document['audit'][2]
        self.assertEqual([c['accepted']
                          for c in call_at(absence, ['columns', 'count'])['candidates']],
                         [False, False])
        self.assertEqual(call_at(absence, ['columns', 'count'])['members'], [])
        self.assertEqual(call_at(absence, ['columns', 'count'])['result'], 0)
        self.assertEqual(call_at(absence, ['columns', 'sum'])['members'], [])
        self.assertEqual(call_at(absence, ['columns', 'sum'])['result'], None)
        # Each output owns an independent evidence list.
        self.assertIsNot(first['calls'][0], second['calls'][0])
        first['calls'][0]['members'].clear()
        self.assertEqual(len(second['calls'][0]['members']), 2)
        self.assertEqual(len(read_audit(self.storage, document['result_id'])
                             ['audit'][0]['calls'][0]['members']), 2)

    def test_count_candidates_include_value_null(self):
        snapshot = self.seed(cal=[{'row_id': 'n', 'station': 'A', 'value': None},
                                  {'row_id': 'x', 'station': 'A', 'value': 0}])
        document = read_audit(self.storage, execute_audited(
            self.storage, compile(self.storage, snapshot, quality_query())))
        count = call_at(document['audit'][0], ['columns', 'count'])
        self.assertEqual([c['source']['row_id'] for c in count['candidates']], ['n', 'x'])
        self.assertEqual([m['value'] for m in count['members']], [1, 1])
        total = call_at(document['audit'][0], ['columns', 'sum'])
        self.assertEqual(total['result'], 0)
        self.assertEqual([m['value'] for m in total['members']], [None, 0])
        self.assertEqual(document['audit'][2]['calls'][0]['result'], 0)

    def test_nested_rejection_and_member_calls(self):
        from audit_cases import rejection_query, member_value_query, fixtures
        obs, cal, _ = fixtures()
        observation = ingest(self.storage, 'observations', 'day', obs)
        calibration = ingest(self.storage, 'calibrations', 'bench', cal)
        snapshot = freeze(self.storage, observation['revision_id'], calibration['revision_id'])
        queries = [rejection_query(), member_value_query()]
        for query in queries:
            result = execute_audited(self.storage, compile(self.storage, snapshot, query))
            document = read_audit(self.storage, result)
            path = ['columns', 'n'] if query == rejection_query() else ['columns', 'total']
            for output in document['audit']:
                call = call_at(output, path)
                if query == rejection_query():
                    self.assertEqual([c['accepted'] for c in call['candidates']],
                                     [True] * 3 + [False] * 3)
                    self.assertEqual([c['calls'][0]['result'] for c in call['candidates']],
                                     [2, 2, 2, 1, 1, 1])
                    self.assertTrue(all(m['value'] == 1 for m in call['members']))
                    self.assertEqual([m['candidate'] for m in call['members']], [0, 1, 2])
                else:
                    self.assertTrue(all(c['accepted'] for c in call['candidates']))
                    self.assertTrue(all(c['calls'] == [] for c in call['candidates']))
                    self.assertEqual([m['value'] for m in call['members']], [2, 2, 2, 1, 1, 1])
                    self.assertTrue(all(len(m['calls']) == 1 for m in call['members']))
                    self.assertEqual(call['result'], 9)

    def test_chained_projection_node_paths(self):
        snapshot = self.seed()
        query = quality_query()
        query = {'op': 'project', 'input': query, 'as': 'p0',
                 'columns': {'c0': col('report', 'count'), 's0': col('report', 'sum')}}
        query = {'op': 'project', 'input': query, 'as': 'p1',
                 'columns': {'c1': col('p0', 'c0'), 's1': col('p0', 's0')}}
        query = {'op': 'project', 'input': query, 'as': 'p2',
                 'columns': {'c2': col('p1', 'c1'), 's2': col('p1', 's1')}}
        document = read_audit(self.storage, execute_audited(
            self.storage, compile(self.storage, snapshot, query)))
        for output, row in zip(document['audit'], document['rows']):
            prefix = ['input', 'input', 'input', 'columns']
            self.assertEqual([c['node'] for c in output['calls']],
                             [prefix + ['count'], prefix + ['sum']])
            self.assertEqual([c['result'] for c in output['calls']],
                             [row['c2'], row['s2']])
        self.assertEqual(document['rows'][:2],
                         [{'c2': 2, 's2': 8}, {'c2': 2, 's2': 8}])

    def test_member_value_nested_calls_are_owned_by_member(self):
        snapshot = self.seed()
        inner = {'op': 'filter', 'input': scan('observations', 'm'),
                 'where': eq(col('m', 'station'), col('s', 'station'))}
        query = quality_query()
        query['columns'] = {'nested': {
            'op': 'sum',
            'query': {'op': 'filter', 'input': scan('calibrations', 'c'),
                      'where': eq(col('c', 'station'), col('s', 'station'))},
            'value': {'op': 'count', 'query': inner}}}
        document = read_audit(self.storage, execute_audited(
            self.storage, compile(self.storage, snapshot, query)))
        call = call_at(document['audit'][0], ['columns', 'nested'])
        self.assertEqual(call['result'], 4)
        self.assertEqual([m['value'] for m in call['members']], [2, 2])
        self.assertTrue(all(c['calls'] == [] for c in call['candidates']))
        self.assertEqual([m['calls'][0]['node'] for m in call['members']],
                         [['columns', 'nested', 'value']] * 2)
        self.assertEqual([len(m['calls'][0]['members']) for m in call['members']], [2, 2])

    def test_filter_after_projection_keeps_evidence(self):
        snapshot = self.seed()
        query = {'op': 'filter', 'input': quality_query(),
                 'where': eq(col('report', 'count'), {'op': 'lit', 'value': 2})}
        document = read_audit(self.storage, execute_audited(
            self.storage, compile(self.storage, snapshot, query)))
        self.assertEqual([o['source']['row_id'] for o in document['audit']], ['o1', 'o2'])
        self.assertEqual([len(o['calls']) for o in document['audit']], [2, 2])

    def test_empty_output_is_valid_audit(self):
        snapshot = self.seed()
        query = {'op': 'filter', 'input': scan('observations', 'x'),
                 'where': eq(col('x', 'station'), {'op': 'lit', 'value': 'none'})}
        result = execute_audited(self.storage, compile(self.storage, snapshot, query))
        document = read_audit(self.storage, result)
        self.assertEqual(document['rows'], [])
        self.assertEqual(document['audit'], [])
        self.assertEqual(json.loads(export_audit(self.storage, result))['audit'], [])
        self.assertEqual(read(self.storage, result)['row_order'], [])

    def test_legacy_result_has_no_audit(self):
        snapshot = self.seed()
        plan = compile(self.storage, snapshot, quality_query())
        legacy = execute(self.storage, plan)
        with self.assertRaises(ResultError):
            read_audit(self.storage, legacy)
        with self.assertRaises(ResultError):
            export_audit(self.storage, legacy)
        with self.assertRaises(ResultError):
            read_audit(self.storage, 999999)
        self.assertEqual(self.count('lp_results'), 1)
        self.assertEqual(self.storage.connection.execute(
            'SELECT count(*) FROM lp_result_audit').fetchone()[0], 0)

    def test_evidence_write_failure_is_atomic(self):
        snapshot = self.seed()
        plan = compile(self.storage, snapshot, quality_query())
        old_id = execute_audited(self.storage, plan)
        old = read_audit(self.storage, old_id)
        before = [self.count(t) for t in ('lp_results', 'lp_result_rows')]
        audit_before = self.storage.connection.execute(
            'SELECT count(*) FROM lp_result_audit').fetchone()[0]
        self.storage.connection.execute(
            "CREATE TRIGGER fail_evidence BEFORE INSERT ON lp_result_audit "
            "BEGIN SELECT RAISE(ABORT,'evidence storage failure'); END")
        try:
            with self.assertRaisesRegex(sqlite3.IntegrityError, 'evidence storage failure'):
                execute_audited(self.storage, plan)
            self.assertEqual([self.count(t) for t in ('lp_results', 'lp_result_rows')], before)
            self.assertEqual(self.storage.connection.execute(
                'SELECT count(*) FROM lp_result_audit').fetchone()[0], audit_before)
            self.assertEqual(read_audit(self.storage, old_id), old)
        finally:
            self.storage.connection.execute('DROP TRIGGER fail_evidence')
        fresh = execute_audited(self.storage, plan)
        self.assertNotEqual(fresh, old_id)
        self.assertEqual(read_audit(self.storage, fresh)['rows'], old['rows'])

    def test_correction_reopen_and_export_isolation(self):
        snapshot = self.seed()
        plan = compile(self.storage, snapshot, quality_query())
        result = execute_audited(self.storage, plan)
        saved = read_audit(self.storage, result)
        ingest(self.storage, 'calibrations', 'bench',
               [dict(row, value=30 if i == 0 else 50)
                for i, row in enumerate(calibrations())])
        changed = copy.deepcopy(saved)
        changed['audit'][0]['calls'][0]['members'].clear()
        changed['rows'][0]['count'] = -1
        self.assertEqual(read_audit(self.storage, result), saved)
        self.assertEqual(json.loads(export_audit(self.storage, result)), saved)
        self.assertEqual(json.loads(export(self.storage, result)), read(self.storage, result))
        with tempfile.TemporaryDirectory(prefix='labpipe-audit-') as directory:
            path = Path(directory) / 'archive.sqlite'
            with Storage.open(path) as disk:
                self.storage.connection.backup(disk.connection)
            with Storage.open(path) as reopened:
                with patch('labpipe.execution.runner.run_plan',
                           side_effect=AssertionError('reexecuted')), \
                        patch('labpipe.catalog.relations.SnapshotRelationReader.scan',
                              side_effect=AssertionError('rescanned')):
                    self.assertEqual(read_audit(reopened, result), saved)
                    target = Path(directory) / 'evidence.json'
                    exported = export_audit(reopened, result, target)
                    self.assertEqual(json.loads(exported), saved)
                    self.assertEqual(json.loads(target.read_text(encoding='utf-8')), saved)
                    self.assertEqual(json.loads(export(reopened, result)),
                                     read(reopened, result))
            with self.assertRaises(OSError):
                with Storage.open(path) as reopened:
                    export_audit(reopened, result, Path(directory) / 'missing' / 'x.json')

    def test_same_row_id_across_batches_and_exact_integers(self):
        observation = ingest(self.storage, 'observations', 'day', observations())
        calibration = ingest(self.storage, 'calibrations', 'bench', calibrations())
        extra = ingest(self.storage, 'calibrations', 'other',
                       [{'row_id': 'c1', 'station': 'A', 'value': 2 ** 70}])
        snapshot = freeze(self.storage, observation['revision_id'], extra['revision_id'])
        document = read_audit(self.storage, execute_audited(
            self.storage, compile(self.storage, snapshot, quality_query())))
        self.assertEqual(document['rows'][0]['sum'], 2 ** 70 + 8)
        self.assertIs(type(document['rows'][0]['sum']), int)
        total = call_at(document['audit'][0], ['columns', 'sum'])
        self.assertEqual([(c['source']['batch_id'], c['source']['row_id'])
                          for c in total['candidates']],
                         [('bench', 'c1'), ('bench', 'c2'), ('other', 'c1')])
        self.assertEqual([m['value'] for m in total['members']], [3, 5, 2 ** 70])

    def test_tampered_evidence_is_rejected(self):
        snapshot = self.seed()
        result = execute_audited(self.storage, compile(self.storage, snapshot, quality_query()))
        saved = self.storage.connection.execute(
            'SELECT doc FROM lp_result_audit WHERE result_id=?', (result,)).fetchone()[0]
        document = json.loads(saved)
        broken = []
        missing_output = copy.deepcopy(document)
        missing_output.pop()
        broken.append(missing_output)
        gap = copy.deepcopy(document)
        gap[1]['ordinal'] = 5
        broken.append(gap)
        wrong_member = copy.deepcopy(document)
        wrong_member[0]['calls'][0]['members'][0]['value'] = 2
        broken.append(wrong_member)
        bad_revision = copy.deepcopy(document)
        bad_revision[0]['source']['revision_id'] = 999
        broken.append(bad_revision)
        bad_accept = copy.deepcopy(document)
        bad_accept[0]['calls'][0]['candidates'][0]['accepted'] = 'yes'
        broken.append(bad_accept)
        for index, evidence in enumerate(broken):
            with self.subTest(index=index):
                self.storage.connection.execute(
                    'UPDATE lp_result_audit SET doc=? WHERE result_id=?',
                    (json.dumps(evidence), result))
                with self.assertRaises(ResultError):
                    read_audit(self.storage, result)
        self.storage.connection.execute(
            'UPDATE lp_result_audit SET doc=? WHERE result_id=?', (saved, result))
        self.assertEqual(read_audit(self.storage, result)['audit'], document)

    def test_missing_evidence_row_and_legacy_store_upgrade(self):
        snapshot = self.seed()
        result = execute_audited(self.storage, compile(self.storage, snapshot, quality_query()))
        self.storage.connection.execute(
            'DELETE FROM lp_result_audit WHERE result_id=?', (result,))
        with self.assertRaises(ResultError):
            read_audit(self.storage, result)
        # A V1 store without the evidence table opens and gains it without data loss.
        with tempfile.TemporaryDirectory(prefix='labpipe-legacy-') as directory:
            path = Path(directory) / 'legacy.sqlite'
            connection = sqlite3.connect(path, isolation_level=None)
            try:
                for statement in (
                        'CREATE TABLE lp_meta(version INTEGER NOT NULL)',
                        'CREATE TABLE lp_revisions(id INTEGER PRIMARY KEY AUTOINCREMENT, table_name TEXT NOT NULL, rows_json TEXT NOT NULL)',
                        'CREATE TABLE lp_current(table_name TEXT PRIMARY KEY, revision_id INTEGER NOT NULL)',
                        'CREATE TABLE lp_batches(table_name TEXT NOT NULL, batch_id TEXT NOT NULL, rows_json TEXT NOT NULL, revision_id INTEGER NOT NULL, PRIMARY KEY(table_name,batch_id))',
                        'CREATE TABLE lp_snapshots(id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT NOT NULL)',
                        'CREATE TABLE lp_plans(id INTEGER PRIMARY KEY AUTOINCREMENT, doc TEXT NOT NULL)',
                        'CREATE TABLE lp_results(id INTEGER PRIMARY KEY AUTOINCREMENT, plan_id INTEGER NOT NULL, provenance TEXT)',
                        'CREATE TABLE lp_result_rows(result_id INTEGER NOT NULL, ordinal INTEGER NOT NULL, row_json TEXT NOT NULL, PRIMARY KEY(result_id,ordinal))'):
                    connection.execute(statement)
                connection.execute('INSERT INTO lp_meta VALUES(1)')
            finally:
                connection.close()
            with Storage.open(path) as upgraded:
                self.assertTrue(upgraded.connection.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='lp_result_audit'").fetchone())
                self.assertEqual(upgraded.connection.execute(
                    'SELECT count(*) FROM lp_meta').fetchone()[0], 1)
