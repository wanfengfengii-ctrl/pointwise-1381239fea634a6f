from labpipe.catalog.relations import SnapshotRelationReader
from labpipe.catalog.snapshots import SnapshotError
from labpipe.planning.format import PlanDocument, PlanError, freeze_query
from labpipe.planning.scopes import ScopeResolver
from labpipe.planning.registry import PlanRegistry
from labpipe.execution.runner import validate_executable


def _shape(node, required, optional=()):
    if type(node) is not dict or not set(required) <= set(node) or set(node) - set(required) - set(optional):
        raise PlanError('invalid query node fields')


def _in_scope(resolver, declaration, action):
    resolver.enter_scope(declaration)
    try:
        return action()
    finally:
        resolver.leave_scope()


def compile_expression(node, resolver, reader):
    if type(node) is not dict:
        raise PlanError('expression must be an object')
    op = node.get('op')
    if op == 'lit':
        _shape(node, ('op', 'value'))
        value = node['value']
        if value is None:
            kind = 'null'
        elif type(value) is int:
            kind = 'int'
        elif type(value) is str:
            kind = 'str'
        else:
            raise PlanError('illegal literal value')
        return {'op': op, 'value': value, 'type': kind}
    if op == 'col':
        _shape(node, ('op', 'name'), ('alias', 'up'))
        binding, kind = resolver.resolve(node['name'], node.get('alias'), node.get('up', 0))
        return {'op': op, 'binding': binding, 'name': node['name'], 'type': kind}
    if op == 'eq':
        _shape(node, ('op', 'left', 'right'))
        left = compile_expression(node['left'], resolver, reader)
        right = compile_expression(node['right'], resolver, reader)
        kinds = {x['type'].rstrip('?') for x in (left, right)} - {'null'}
        if len(kinds) > 1 or 'predicate' in kinds:
            raise PlanError('incompatible equality operands')
        return {'op': op, 'left': left, 'right': right, 'type': 'predicate'}
    if op in ('count', 'sum'):
        _shape(node, ('op', 'query') + (('value',) if op == 'sum' else ()))
        relation, declaration = compile_relation(node['query'], resolver, reader)
        result = {'op': op, 'query': relation, 'type': 'int' if op == 'count' else 'int?'}
        if op == 'sum':
            value = _in_scope(resolver, declaration,
                              lambda: compile_expression(node['value'], resolver, reader))
            if value['type'].rstrip('?') not in ('int', 'null'):
                raise PlanError('sum requires an integer or nullable integer expression')
            result['value'] = value
        return result
    raise PlanError('unknown expression operator: ' + str(op))


def compile_relation(node, resolver, reader):
    if type(node) is not dict:
        raise PlanError('relation must be an object')
    op = node.get('op')
    if op == 'scan':
        _shape(node, ('op', 'table', 'as'))
        try:
            schema = reader.schema(node['table'])
        except (SnapshotError, TypeError) as exc:
            raise PlanError('invalid scan table') from exc
        declaration = resolver.declare(node['as'], schema)
        return {'op': op, 'table': node['table'], 'binding': declaration['binding'],
                'types': schema}, declaration
    if op == 'filter':
        _shape(node, ('op', 'input', 'where'))
        source, declaration = compile_relation(node['input'], resolver, reader)
        where = _in_scope(resolver, declaration,
                          lambda: compile_expression(node['where'], resolver, reader))
        if where['type'] != 'predicate':
            raise PlanError('filter requires an equality predicate')
        return {'op': op, 'input': source, 'where': where,
                'types': source['types']}, declaration
    if op == 'project':
        _shape(node, ('op', 'input', 'as', 'columns'))
        columns = node['columns']
        if type(columns) is not dict or not columns:
            raise PlanError('project requires nonempty named columns')
        source, declaration = compile_relation(node['input'], resolver, reader)
        compiled = {}
        resolver.enter_scope(declaration)
        try:
            for name, expression in columns.items():
                if type(name) is not str or not name.strip():
                    raise PlanError('invalid output column name')
                value = compile_expression(expression, resolver, reader)
                if value['type'] == 'predicate':
                    raise PlanError('predicates cannot be business row values')
                compiled[name] = value
        finally:
            resolver.leave_scope()
        types = {name: value['type'] for name, value in compiled.items()}
        output = resolver.declare(node['as'], types)
        return {'op': op, 'input': source, 'columns': compiled,
                'binding': output['binding'], 'types': types}, output
    raise PlanError('unknown relation operator: ' + str(op))


def compile(storage, snapshot_id, query_tree):
    query = freeze_query(query_tree)
    try:
        reader = SnapshotRelationReader(storage, snapshot_id)
    except SnapshotError as exc:
        raise PlanError(str(exc)) from exc
    root, _ = compile_relation(query, ScopeResolver(), reader)
    document = PlanDocument(snapshot_id, query, root, dict(root['types']))
    validate_executable(document)
    return PlanRegistry(storage).save_plan(document)
