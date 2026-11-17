import copy
import json
import tempfile
from pathlib import Path
from labpipe.catalog.storage import Storage
from labpipe.planning.compiler import compile
from labpipe.planning.registry import PlanRegistry
from labpipe.planning.format import PlanError, PlanDocument
from labpipe.execution.runner import validate_executable
from labpipe.workflow.processing import execute
from labpipe.results.reader import read
from tests.test_intake import BusinessCase, quality_query, col, scan, eq, EXPECTED


def nested_query(outer='s', inner='s'):
    query = quality_query(outer, 'unused')
    deepest = {'op': 'filter', 'input': scan('calibrations', inner),
               'where': eq(col(inner, 'station'), col(outer, 'station', up=2))}
    middle = {'op': 'filter', 'input': scan('observations', 'm'),
              'where': eq(col('m', 'row_id'), col(outer, 'row_id'))}
    for output, aggregate in [('count', 'count'), ('sum', 'sum')]:
        scalar = {'op': aggregate, 'query': copy.deepcopy(deepest)}
        if aggregate == 'sum':
            scalar['value'] = col(inner, 'value')
        projected = {'op': 'project', 'input': copy.deepcopy(middle), 'as': 't',
                     'columns': {'number': scalar}}
        query['columns'][output] = {'op': 'sum', 'query': projected,
                                    'value': col('t', 'number')}
    query['columns']['after'] = col(outer, 'station')
    return query


class TestPlans(BusinessCase):
    def test_nested_shadowing_alpha_rename(self):
        snapshot = self.seed()
        expected = [dict(row, after=row['station']) for row in EXPECTED]
        for query in (nested_query(), nested_query('outer_station', 'calibration')):
            plan = compile(self.storage, snapshot, query)
            document = PlanRegistry(self.storage).load_plan(plan)
            self.assertEqual(document.query, query)
            self.assertEqual(read(self.storage, execute(self.storage, plan))['rows'], expected)

    def test_invalid_reference_rejected(self):
        snapshot = self.seed()
        for expression in [col('missing', 'station'), col('s', 'absent'),
                           col('s', 'station', 9), col('s', 'station', -1),
                           col('s', 'station', True)]:
            query = quality_query()
            query['columns']['broken'] = expression
            with self.subTest(expression=expression), self.assertRaises(PlanError):
                compile(self.storage, snapshot, query)
            self.assertEqual(self.count('lp_plans'), 0)

    def test_types_and_ambiguity(self):
        snapshot = self.seed()
        queries = []
        bad_sum = quality_query()
        bad_sum['columns']['sum']['value'] = col('c', 'station')
        queries.append(bad_sum)
        ambiguous = quality_query()
        ambiguous['columns']['count']['query']['where']['right'] = {'op': 'col', 'name': 'station'}
        queries.append(ambiguous)
        literal = quality_query()
        literal['columns']['bad'] = {'op': 'lit', 'value': True}
        queries.append(literal)
        invalid_filter = quality_query()
        invalid_filter['columns']['count']['query']['where'] = {'op': 'lit', 'value': 1}
        queries.append(invalid_filter)
        predicate = quality_query()
        predicate['columns']['bad'] = eq(col('s', 'station'), {'op': 'lit', 'value': 'A'})
        queries.append(predicate)
        for query in queries:
            with self.subTest(query=query), self.assertRaises(PlanError):
                compile(self.storage, snapshot, query)
            self.assertEqual(self.count('lp_plans'), 0)

    def test_operator_rejection_and_preflight(self):
        snapshot = self.seed()
        for query in [{'op': 'join'}, {'op': 'scan', 'table': 'unknown', 'as': 's'},
                      {'op': 'scan', 'table': 'observations', 'as': 's', 'extra': 1}]:
            with self.assertRaises(PlanError):
                compile(self.storage, snapshot, query)
        plan = compile(self.storage, snapshot, quality_query())
        document = PlanRegistry(self.storage).load_plan(plan)
        data = copy.deepcopy(document.to_dict())
        data['root']['columns']['station']['binding'] = 999
        with self.assertRaises(PlanError):
            PlanRegistry(self.storage).save_plan(PlanDocument(**data))
        data['root'] = {'op': 'alien'}
        with self.assertRaises(PlanError):
            validate_executable(PlanDocument(**data))
        self.assertEqual(self.count('lp_plans'), 1)

    def test_persisted_bindings(self):
        snapshot = self.seed()
        plans = [compile(self.storage, snapshot, nested_query()),
                 compile(self.storage, snapshot, nested_query('o', 'c'))]
        with tempfile.TemporaryDirectory(prefix='labpipe-plan-') as directory:
            path = Path(directory) / 'plans.sqlite'
            with Storage.open(path) as disk:
                self.storage.connection.backup(disk.connection)
            with Storage.open(path) as reopened:
                actual = [read(reopened, execute(reopened, plan))['rows'] for plan in plans]
                self.assertEqual(actual[0], [dict(row, after=row['station']) for row in EXPECTED])
                self.assertEqual(actual[0], actual[1])
                saved = PlanRegistry(reopened).load_plan(plans[0]).to_dict()
                self.assertNotIn('"alias"', json.dumps(saved['root']))
