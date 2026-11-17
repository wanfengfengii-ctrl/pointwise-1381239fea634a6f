import argparse
import json
import sqlite3
import tempfile
from pathlib import Path
from labpipe.catalog.storage import Storage
from labpipe.catalog.snapshots import freeze, SnapshotError
from labpipe.catalog.relations import SnapshotRelationReader
from labpipe.workflow.intake import ingest, ingest_sample
from labpipe.batch.validation import BatchError
from labpipe.planning.compiler import compile
from labpipe.planning.registry import PlanRegistry
from labpipe.planning.format import PlanError
from labpipe.execution.runner import run_plan
from labpipe.results.publication import publish_result
from labpipe.results.reader import read
from labpipe.results.export import export
from labpipe.results.codec import ResultError


def execute(storage, plan_id):
    document = PlanRegistry(storage).load_plan(plan_id)
    reader = SnapshotRelationReader(storage, document.snapshot_id)
    rows = run_plan(document, reader)
    return publish_result(storage, plan_id, rows)


def process_day(storage, observation_revision, calibration_revision, query_tree):
    snapshot_id = freeze(storage, observation_revision, calibration_revision)
    plan_id = compile(storage, snapshot_id, query_tree)
    return {'snapshot_id': snapshot_id, 'plan_id': plan_id,
            'result_id': execute(storage, plan_id)}


def _demo(directory, sample_path):
    sample = json.loads(Path(sample_path).read_text(encoding='utf-8'))
    database = Path(directory) / 'labpipe.sqlite'
    with Storage.open(database) as storage:
        receipts = ingest_sample(storage, sample_path)
        operation = process_day(storage, receipts['observations']['revision_id'],
                                receipts['calibrations']['revision_id'], sample['query'])
        plan_id = operation['plan_id']
        result_id = operation['result_id']
        original = read(storage, result_id)
        assert original['rows'] == sample['expected']
        changed = [dict(row, value=30 if i == 0 else 50)
                   for i, row in enumerate(sample['calibrations']['rows'])]
        ingest(storage, 'calibrations', sample['calibrations']['batch_id'], changed)
    with Storage.open(database) as storage:
        repeated = read(storage, execute(storage, plan_id))
        assert repeated['rows'] == sample['expected']
        exported = json.loads(export(storage, result_id, Path(directory) / 'quality.json'))
        assert exported == original
        print('POINTWISE_SCENARIO ' + json.dumps({
            'case_id': 'V8.export_roundtrip', 'input': {
                'observations': sample['observations']['rows'],
                'calibrations': sample['calibrations']['rows'],
                'later_calibrations': changed},
            'expected': sample['expected'], 'actual': repeated['rows'],
            'downstream_expected': original, 'downstream_actual': exported,
            'code_paths': ['labpipe/planning/compiler.py', 'labpipe/execution/runner.py',
                           'labpipe/results/publication.py', 'labpipe/results/export.py'],
            'oracle': 'A has two calibrations: 3+5=8; B has none; three observation identities',
        }, ensure_ascii=False))


def main(argv=None):
    parser = argparse.ArgumentParser(description='Environmental laboratory fixed-input processing')
    parser.add_argument('--db', default='labpipe.sqlite')
    commands = parser.add_subparsers(dest='command', required=True)
    intake = commands.add_parser('ingest')
    intake.add_argument('table', choices=('observations', 'calibrations'))
    intake.add_argument('batch_id')
    intake.add_argument('rows_file')
    frozen = commands.add_parser('freeze')
    frozen.add_argument('observation_revision', type=int)
    frozen.add_argument('calibration_revision', type=int)
    planning = commands.add_parser('compile')
    planning.add_argument('snapshot_id', type=int)
    planning.add_argument('query_file')
    running = commands.add_parser('execute')
    running.add_argument('plan_id', type=int)
    reading = commands.add_parser('read')
    reading.add_argument('result_id', type=int)
    exporting = commands.add_parser('export')
    exporting.add_argument('result_id', type=int)
    exporting.add_argument('output_file')
    demo = commands.add_parser('demo')
    demo.add_argument('--directory')
    demo.add_argument('--sample', default='examples.json')
    args = parser.parse_args(argv)
    try:
        if args.command == 'demo':
            if args.directory is None:
                with tempfile.TemporaryDirectory(prefix='labpipe-demo-') as directory:
                    _demo(directory, args.sample)
            else:
                Path(args.directory).mkdir(parents=True, exist_ok=True)
                _demo(args.directory, args.sample)
            return 0
        with Storage.open(args.db) as storage:
            if args.command == 'ingest':
                rows = json.loads(Path(args.rows_file).read_text(encoding='utf-8'))
                result = ingest(storage, args.table, args.batch_id, rows)
            elif args.command == 'freeze':
                result = {'snapshot_id': freeze(storage, args.observation_revision, args.calibration_revision)}
            elif args.command == 'compile':
                query = json.loads(Path(args.query_file).read_text(encoding='utf-8'))
                result = {'plan_id': compile(storage, args.snapshot_id, query)}
            elif args.command == 'execute':
                result = {'result_id': execute(storage, args.plan_id)}
            elif args.command == 'read':
                result = read(storage, args.result_id)
            else:
                result = json.loads(export(storage, args.result_id, args.output_file))
            print(json.dumps(result, ensure_ascii=False))
        return 0
    except (BatchError, SnapshotError, PlanError, ResultError, sqlite3.Error, OSError, ValueError) as exc:
        print(json.dumps({'error': type(exc).__name__, 'message': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
