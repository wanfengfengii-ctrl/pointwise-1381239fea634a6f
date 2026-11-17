import json
from collections import Counter
from labpipe.planning.compiler import compile
from labpipe.workflow.processing import execute
from labpipe.results.reader import read
from labpipe.results.export import export
from tests.test_intake import BusinessCase, quality_query, observations, EXPECTED, col, scan, eq
from tests.test_plans import nested_query


def bag(rows):
    return Counter(json.dumps(row, sort_keys=True) for row in rows)


class TestProcessing(BusinessCase):
    def test_station_calibration(self):
        snapshot = self.seed()
        result = read(self.storage, execute(self.storage, compile(self.storage, snapshot, quality_query())))
        self.assertEqual(result['rows'], EXPECTED)
        self.assertEqual(result['row_order'], [0, 1, 2])
        exported = json.loads(export(self.storage, result['result_id']))
        self.assertEqual(exported, result)
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'V3', 'input': {'observations': observations(), 'calibrations': [3, 5]},
            'expected': EXPECTED, 'actual': result['rows'],
            'downstream_expected': EXPECTED, 'downstream_actual': exported['rows'],
            'code_paths': ['labpipe/batch/revisions.py', 'labpipe/planning/compiler.py',
                           'labpipe/execution/expressions.py', 'labpipe/results/export.py'],
            'oracle': 'Independent hand tally: A count=2 sum=3+5; B count=0 sum=null',
        }))

    def test_environment_restored_after_empty_aggregate(self):
        expected = [dict(row, after=row['station']) for row in EXPECTED]
        for rows in (observations(), list(reversed(observations())),
                     [observations()[0], observations()[2], observations()[1]]):
            snapshot = self.seed(obs=rows)
            result = read(self.storage, execute(self.storage, compile(self.storage, snapshot, nested_query())))
            self.assertEqual(bag(result['rows']), bag(expected))

    def test_project_bag_and_plain_processing(self):
        snapshot = self.seed()
        query = {'op': 'project', 'input': scan('observations', 's'), 'as': 'p',
                 'columns': {'v': col('s', 'value')}}
        plan = compile(self.storage, snapshot, query)
        self.assertEqual(read(self.storage, execute(self.storage, plan))['rows'],
                         [{'v': 10}, {'v': 10}, {'v': 20}])
        filtered = {'op': 'filter', 'input': query,
                    'where': eq(col('p', 'v'), {'op': 'lit', 'value': 10})}
        result = read(self.storage, execute(self.storage, compile(self.storage, snapshot, filtered)))
        self.assertEqual(result['rows'], [{'v': 10}, {'v': 10}])

    def test_null_and_exact_sum(self):
        huge = 2 ** 70
        calibration = [{'row_id': 'x', 'station': 'A', 'value': huge},
                       {'row_id': 'y', 'station': 'A', 'value': 3},
                       {'row_id': 'z', 'station': 'A', 'value': None},
                       {'row_id': 'b', 'station': 'B', 'value': None}]
        snapshot = self.seed(cal=calibration)
        result = read(self.storage, execute(self.storage, compile(self.storage, snapshot, quality_query())))
        self.assertEqual(result['rows'], [
            {'row_id': 'o1', 'station': 'A', 'count': 3, 'sum': 1180591620717411303427},
            {'row_id': 'o2', 'station': 'A', 'count': 3, 'sum': 1180591620717411303427},
            {'row_id': 'o3', 'station': 'B', 'count': 1, 'sum': None}])
        self.assertIs(type(result['rows'][0]['sum']), int)

    def test_empty_scalar_and_empty_observations(self):
        snapshot = self.seed(cal=[])
        result = read(self.storage, execute(self.storage, compile(self.storage, snapshot, quality_query())))
        self.assertEqual(result['rows'], [dict(row, count=0, sum=None) for row in EXPECTED])
        snapshot = self.seed(obs=[])
        result = read(self.storage, execute(self.storage, compile(self.storage, snapshot, quality_query())))
        self.assertEqual(result['rows'], [])
        self.assertEqual(result['provenance']['row_count'], 0)

    def test_null_equality_and_zero_sum(self):
        snapshot = self.seed(cal=[{'row_id': 'zero', 'station': 'A', 'value': 0}])
        query = quality_query()
        query['columns']['null_count'] = {
            'op': 'count', 'query': {'op': 'filter', 'input': scan('calibrations', 'n'),
                                    'where': eq({'op': 'lit', 'value': None},
                                                {'op': 'lit', 'value': None})}}
        result = read(self.storage, execute(self.storage, compile(self.storage, snapshot, query)))
        self.assertEqual([row['null_count'] for row in result['rows']], [0, 0, 0])
        self.assertEqual([row['sum'] for row in result['rows']], [0, 0, None])
