from labpipe.execution.aggregates import count_rows, sum_values


def evaluate_predicate(node, environment, run_relation):
    left = evaluate_expression(node['left'], environment, run_relation)
    right = evaluate_expression(node['right'], environment, run_relation)
    if left is None or right is None:
        return None
    return left == right


def evaluate_scalar_aggregate(node, environment, run_relation):
    with environment.child_scope() as child:
        rows = run_relation(node['query'], child)
        try:
            if node['op'] == 'count':
                return count_rows(rows)
            return sum_values(evaluate_expression(node['value'], row_environment, run_relation)
                              for _, row_environment in rows)
        finally:
            rows.close()


def evaluate_expression(node, environment, run_relation):
    op = node['op']
    if op == 'lit':
        return node['value']
    if op == 'col':
        return environment.resolve(node['binding'], node['name'])
    if op == 'eq':
        return evaluate_predicate(node, environment, run_relation)
    if op in ('count', 'sum'):
        return evaluate_scalar_aggregate(node, environment, run_relation)
    raise RuntimeError('unexecutable expression: ' + str(op))
