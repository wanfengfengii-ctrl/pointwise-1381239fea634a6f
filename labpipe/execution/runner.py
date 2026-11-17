from labpipe.planning.format import PlanDocument, PlanError, PLAN_FORMAT_VERSION
from labpipe.catalog.schemas import get_schema
from labpipe.execution.environments import RowEnvironment
from labpipe.execution.relations import iter_relation
from labpipe.execution.expressions import evaluate_expression


class ExecutionError(RuntimeError):
    pass


def validate_executable(document):
    if not isinstance(document, PlanDocument) or document.version != PLAN_FORMAT_VERSION:
        raise PlanError('unsupported executable document')
    declarations = set()
    legal_types = {'int', 'int?', 'str', 'null'}

    def fields(node, expected):
        if type(node) is not dict or set(node) != set(expected):
            raise PlanError('malformed executable node')

    def types(value):
        if type(value) is not dict or not value:
            raise PlanError('invalid executable schema')
        if any(type(k) is not str or not k.strip() or v not in legal_types
               for k, v in value.items()):
            raise PlanError('invalid executable column type')
        return value

    def declare(node):
        identity = node['binding']
        if type(identity) is not int or identity <= 0 or identity in declarations:
            raise PlanError('duplicate or invalid binding identity')
        declarations.add(identity)
        return {identity: types(node['types'])}

    def expression(node, available):
        if type(node) is not dict:
            raise PlanError('invalid executable expression')
        op = node.get('op')
        if op == 'lit':
            fields(node, ('op', 'value', 'type'))
            value = node['value']
            kind = 'null' if value is None else 'int' if type(value) is int else 'str' if type(value) is str else None
            if kind is None:
                raise PlanError('invalid executable literal')
        elif op == 'col':
            fields(node, ('op', 'binding', 'name', 'type'))
            identity = node['binding']
            if type(identity) is not int or identity not in available or node['name'] not in available[identity]:
                raise PlanError('executable reference is outside its declared environment')
            kind = available[identity][node['name']]
        elif op == 'eq':
            fields(node, ('op', 'left', 'right', 'type'))
            operands = {expression(node[k], available).rstrip('?') for k in ('left', 'right')} - {'null'}
            if len(operands) > 1 or 'predicate' in operands:
                raise PlanError('invalid executable equality')
            kind = 'predicate'
        elif op in ('count', 'sum'):
            fields(node, ('op', 'query', 'type') + (('value',) if op == 'sum' else ()))
            local = relation(node['query'], available)
            kind = 'int' if op == 'count' else 'int?'
            if op == 'sum' and expression(node['value'], available | local).rstrip('?') not in ('int', 'null'):
                raise PlanError('invalid executable sum')
        else:
            raise PlanError('unsupported executable expression')
        if node['type'] != kind:
            raise PlanError('inconsistent executable expression type')
        return kind

    def relation(node, outer):
        if type(node) is not dict:
            raise PlanError('invalid executable relation')
        op = node.get('op')
        if op == 'scan':
            fields(node, ('op', 'table', 'binding', 'types'))
            try:
                schema = get_schema(node['table'])
            except (ValueError, TypeError) as exc:
                raise PlanError('invalid executable scan') from exc
            if node['types'] != schema:
                raise PlanError('scan schema mismatch')
            return declare(node)
        if op == 'filter':
            fields(node, ('op', 'input', 'where', 'types'))
            local = relation(node['input'], outer)
            if node['types'] != node['input']['types']:
                raise PlanError('filter schema mismatch')
            if expression(node['where'], outer | local) != 'predicate':
                raise PlanError('invalid executable predicate')
            return local
        if op == 'project':
            fields(node, ('op', 'input', 'columns', 'binding', 'types'))
            local = relation(node['input'], outer)
            if type(node['columns']) is not dict or not node['columns']:
                raise PlanError('invalid executable projection')
            schema = {name: expression(value, outer | local)
                      for name, value in node['columns'].items()}
            if schema != node['types']:
                raise PlanError('projection schema mismatch')
            return declare(node)
        raise PlanError('unsupported executable relation')

    try:
        relation(document.root, {})
        if document.output_types != document.root['types']:
            raise PlanError('output schema mismatch')
    except (KeyError, TypeError, RecursionError) as exc:
        raise PlanError('malformed executable plan') from exc
    return document


def run_plan(document, reader):
    validate_executable(document)
    if reader.snapshot['snapshot_id'] != document.snapshot_id:
        raise ExecutionError('plan and fixed relation reader belong to different inputs')
    with RowEnvironment().child_scope() as root_environment:
        def evaluate(node, environment):
            return evaluate_expression(node, environment, run_relation)

        def run_relation(node, environment):
            return iter_relation(node, environment, reader, evaluate)

        iterator = run_relation(document.root, root_environment)
        try:
            return [dict(row) for row, _ in iterator]
        finally:
            iterator.close()
