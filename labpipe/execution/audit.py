"""Instrumented evaluation that records per-output source identity and aggregate calls.

The business row stream is produced by the same relation/expression semantics as
``labpipe.execution.runner``; the extra bookkeeping never changes evaluation
order or short-circuit behaviour.
"""
from labpipe.planning.format import PlanError
from labpipe.execution.environments import RowEnvironment
from labpipe.execution.runner import ExecutionError, validate_executable


class _Candidate:
    """One row occurrence of a relation's driving scan while it travels the chain."""

    __slots__ = ('source', 'accepted', 'row', 'environment', 'calls')

    def __init__(self, source, accepted, row, environment, calls):
        self.source = source
        self.accepted = accepted
        self.row = row
        self.environment = environment
        self.calls = calls


def _source(reader, table, row):
    return {'revision_id': reader.snapshot['tables'][table]['revision_id'],
            'table': table, 'batch_id': row['batch_id'], 'row_id': row['row_id']}


def enumerate_relation(node, environment, reader, path):
    """Return every driving-scan occurrence with its accumulated direct calls.

    Rejected occurrences stay in the list (with their already-performed calls);
    accepted ones are exactly the rows of the relation.
    """
    op = node['op']
    if op == 'scan':
        table = node['table']
        candidates = []
        for row in reader.scan(table):
            row = dict(row)
            candidates.append(_Candidate(
                _source(reader, table, row), True, row,
                environment.extend(node['binding'], row), []))
        return candidates
    if op == 'filter':
        candidates = enumerate_relation(node['input'], environment, reader, path + ['input'])
        where_path = path + ['where']
        for candidate in candidates:
            if not candidate.accepted:
                continue
            value = evaluate_audited(node['where'], candidate.environment, reader,
                                     candidate.calls, where_path)
            candidate.accepted = value is True
        return candidates
    if op == 'project':
        candidates = enumerate_relation(node['input'], environment, reader, path + ['input'])
        for candidate in candidates:
            if not candidate.accepted:
                continue
            row = {}
            for name, expression in node['columns'].items():
                row[name] = evaluate_audited(expression, candidate.environment, reader,
                                             candidate.calls, path + ['columns', name])
            candidate.row = row
            candidate.environment = environment.extend(node['binding'], row)
        return candidates
    raise RuntimeError('unexecutable relation: ' + str(op))


def _aggregate(node, environment, reader, context, path):
    op = node['op']
    with environment.child_scope() as child:
        candidates = enumerate_relation(node['query'], child, reader, path + ['query'])
        recorded = [{'ordinal': ordinal, 'source': candidate.source,
                     'accepted': candidate.accepted, 'calls': candidate.calls}
                    for ordinal, candidate in enumerate(candidates)]
        members = []
        values = []
        for ordinal, candidate in enumerate(candidates):
            if not candidate.accepted:
                continue
            if op == 'count':
                members.append({'candidate': ordinal, 'value': 1, 'calls': []})
                continue
            member_calls = []
            value = evaluate_audited(node['value'], candidate.environment, reader,
                                     member_calls, path + ['value'])
            if value is not None and type(value) is not int:
                raise PlanError('noninteger value reached integer aggregate')
            members.append({'candidate': ordinal, 'value': value, 'calls': member_calls})
            values.append(value)
    if op == 'count':
        result = len(members)
    else:
        nonnull = [value for value in values if value is not None]
        result = sum(nonnull) if nonnull else None
    context.append({'node': list(path), 'op': op, 'result': result,
                    'candidates': recorded, 'members': members})
    return result


def evaluate_audited(node, environment, reader, context, path):
    op = node['op']
    if op == 'lit':
        return node['value']
    if op == 'col':
        return environment.resolve(node['binding'], node['name'])
    if op == 'eq':
        left = evaluate_audited(node['left'], environment, reader, context,
                                path + ['left'])
        right = evaluate_audited(node['right'], environment, reader, context,
                                 path + ['right'])
        if left is None or right is None:
            return None
        return left == right
    if op in ('count', 'sum'):
        return _aggregate(node, environment, reader, context, path)
    raise RuntimeError('unexecutable expression: ' + str(op))


def run_audited(document, reader):
    """Return ``(rows, audit)`` for a persisted plan and its fixed input."""
    validate_executable(document)
    if reader.snapshot['snapshot_id'] != document.snapshot_id:
        raise ExecutionError('plan and fixed relation reader belong to different inputs')
    with RowEnvironment().child_scope() as root_environment:
        candidates = enumerate_relation(document.root, root_environment, reader, [])
    rows = [dict(candidate.row) for candidate in candidates if candidate.accepted]
    audit = []
    for candidate in candidates:
        if not candidate.accepted:
            continue
        audit.append({'ordinal': len(audit), 'source': candidate.source,
                      'calls': candidate.calls})
    return rows, audit
