import copy
import unittest
from labpipe.catalog.storage import Storage
from labpipe.batch.revisions import RevisionStore
from labpipe.batch.validation import BatchError
from labpipe.catalog.snapshots import freeze, get_snapshot, SnapshotError
from labpipe.catalog.relations import SnapshotRelationReader
from labpipe.workflow.intake import ingest


def observations():
    return [{'row_id': 'o1', 'station': 'A', 'value': 10},
            {'row_id': 'o2', 'station': 'A', 'value': 10},
            {'row_id': 'o3', 'station': 'B', 'value': 20}]


def calibrations():
    return [{'row_id': 'c1', 'station': 'A', 'value': 3},
            {'row_id': 'c2', 'station': 'A', 'value': 5}]


def col(alias, name, up=0):
    return {'op': 'col', 'alias': alias, 'name': name, 'up': up}


def scan(table, alias):
    return {'op': 'scan', 'table': table, 'as': alias}


def eq(left, right):
    return {'op': 'eq', 'left': left, 'right': right}


def quality_query(outer='s', inner='c'):
    matched = {'op': 'filter', 'input': scan('calibrations', inner),
               'where': eq(col(inner, 'station'), col(outer, 'station'))}
    return {'op': 'project', 'input': scan('observations', outer), 'as': 'report',
            'columns': {'row_id': col(outer, 'row_id'), 'station': col(outer, 'station'),
                        'count': {'op': 'count', 'query': copy.deepcopy(matched)},
                        'sum': {'op': 'sum', 'query': matched, 'value': col(inner, 'value')}}}


EXPECTED = [
    {'row_id': 'o1', 'station': 'A', 'count': 2, 'sum': 8},
    {'row_id': 'o2', 'station': 'A', 'count': 2, 'sum': 8},
    {'row_id': 'o3', 'station': 'B', 'count': 0, 'sum': None},
]


class BusinessCase(unittest.TestCase):
    def setUp(self):
        self.storage = Storage.open()
        self.addCleanup(self.storage.close)
        self.revisions = RevisionStore(self.storage)

    def seed(self, obs=None, cal=None):
        observation = ingest(self.storage, 'observations', 'day',
                             observations() if obs is None else obs)
        calibration = ingest(self.storage, 'calibrations', 'bench',
                             calibrations() if cal is None else cal)
        return freeze(self.storage, observation['revision_id'], calibration['revision_id'])

    def count(self, table):
        allowed = {'lp_revisions', 'lp_batches', 'lp_snapshots', 'lp_plans',
                   'lp_results', 'lp_result_rows'}
        if table not in allowed:
            raise ValueError(table)
        return self.storage.connection.execute('SELECT count(*) FROM ' + table).fetchone()[0]


class TestIntake(BusinessCase):
    def test_distinct_duplicate_observations(self):
        snapshot = self.seed()
        rows = list(SnapshotRelationReader(self.storage, snapshot).scan('observations'))
        self.assertEqual([r['row_id'] for r in rows], ['o1', 'o2', 'o3'])
        self.assertEqual(rows[0]['value'], rows[1]['value'])
        self.assertEqual(len(rows), 3)

    def test_invalid_batch_atomic(self):
        snapshot = self.seed()
        original = self.revisions.current_revision('observations')
        frozen = get_snapshot(self.storage, snapshot)
        before = [self.count(t) for t in ('lp_revisions', 'lp_batches', 'lp_snapshots')]
        invalid = [
            observations() + [dict(observations()[0])],
            observations() + [{'row_id': 'late', 'station': 'B', 'value': True}],
        ]
        for rows in invalid:
            with self.subTest(rows=rows), self.assertRaises(BatchError):
                ingest(self.storage, 'observations', 'day', rows)
            self.assertEqual(original, self.revisions.current_revision('observations'))
            self.assertEqual(before, [self.count(t) for t in ('lp_revisions', 'lp_batches', 'lp_snapshots')])
            self.assertEqual(get_snapshot(self.storage, snapshot), frozen)

    def test_value_types(self):
        for table in ('observations', 'calibrations'):
            for field, value in [('value', True), ('value', 1.25), ('value', '3'),
                                 ('station', ''), ('station', None), ('row_id', ' ')]:
                row = {'row_id': 'id', 'station': 'A', 'value': 3}
                row[field] = value
                with self.subTest(table=table, field=field, value=value), self.assertRaises(BatchError):
                    ingest(self.storage, table, 'bad', [row])
                self.assertIsNone(self.revisions.current_revision(table))
        with self.assertRaises(BatchError):
            ingest(self.storage, 'other', 'bad', [])
        with self.assertRaises(BatchError):
            ingest(self.storage, 'observations', '', [])
        with self.assertRaises(BatchError):
            ingest(self.storage, 'observations', 'bad', [{'row_id': 'x', 'station': 'A', 'value': 1, 'extra': 2}])

    def test_replay_and_revision_history(self):
        snapshot = self.seed()
        first = self.revisions.get_receipt('observations', 'day')
        before = self.count('lp_revisions')
        self.assertEqual(ingest(self.storage, 'observations', 'day', observations()), first)
        self.assertEqual(self.count('lp_revisions'), before)
        other = ingest(self.storage, 'observations', 'other',
                       [{'row_id': 'o1', 'station': 'C', 'value': 4}])
        self.assertEqual(ingest(self.storage, 'observations', 'day', observations()), first)
        self.assertEqual(self.revisions.current_revision('observations'), other['revision_id'])
        changed = ingest(self.storage, 'observations', 'day', observations()[:1])
        self.assertNotEqual(changed['revision_id'], first['revision_id'])
        self.assertEqual(len(self.revisions.read_revision(first['revision_id'])), 3)
        self.assertEqual([r['batch_id'] for r in self.revisions.read_revision(changed['revision_id'])],
                         ['day', 'other'])
        self.assertEqual(len(list(SnapshotRelationReader(self.storage, snapshot).scan('observations'))), 3)
        cleared = ingest(self.storage, 'observations', 'day', [])
        self.assertEqual(len(self.revisions.read_revision(cleared['revision_id'])), 1)

    def test_freeze_unknown(self):
        self.seed()
        observation = self.revisions.current_revision('observations')
        calibration = self.revisions.current_revision('calibrations')
        before = self.count('lp_snapshots')
        for pair in [(observation, 999), (999, calibration), (calibration, observation),
                     (True, calibration)]:
            with self.subTest(pair=pair), self.assertRaises(SnapshotError):
                freeze(self.storage, *pair)
            self.assertEqual(self.count('lp_snapshots'), before)

    def test_source_iterator_failure_is_atomic(self):
        self.seed()
        before = self.revisions.current_revision('observations')
        def source():
            yield observations()[0]
            raise OSError('upstream read failed')
        with self.assertRaisesRegex(OSError, 'upstream'):
            ingest(self.storage, 'observations', 'new', source())
        self.assertEqual(self.revisions.current_revision('observations'), before)
