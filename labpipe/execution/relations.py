def iter_scan(node, parent, reader):
    for row in reader.scan(node['table']):
        yield row, parent.extend(node['binding'], row)


def iter_filter(node, parent, reader, evaluate):
    for row, environment in iter_relation(node['input'], parent, reader, evaluate):
        if evaluate(node['where'], environment) is True:
            yield row, environment


def iter_project(node, parent, reader, evaluate):
    for _, environment in iter_relation(node['input'], parent, reader, evaluate):
        row = {name: evaluate(expression, environment)
               for name, expression in node['columns'].items()}
        yield row, parent.extend(node['binding'], row)


def iter_relation(node, parent, reader, evaluate):
    op = node['op']
    if op == 'scan':
        yield from iter_scan(node, parent, reader)
    elif op == 'filter':
        yield from iter_filter(node, parent, reader, evaluate)
    elif op == 'project':
        yield from iter_project(node, parent, reader, evaluate)
    else:
        raise RuntimeError('unexecutable relation: ' + str(op))
