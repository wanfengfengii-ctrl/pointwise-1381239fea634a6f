from contextlib import contextmanager


class RowEnvironment:
    def __init__(self, bindings=None):
        self._bindings = {} if bindings is None else {
            identity: dict(row) for identity, row in bindings.items()
        }

    def extend(self, binding, row):
        bindings = dict(self._bindings)
        bindings[binding] = dict(row)
        return RowEnvironment(bindings)

    def resolve(self, binding, column):
        try:
            return self._bindings[binding][column]
        except KeyError as exc:
            raise RuntimeError(f'unavailable bound column {binding}.{column}') from exc

    @contextmanager
    def child_scope(self):
        child = RowEnvironment(self._bindings)
        try:
            yield child
        finally:
            child._bindings.clear()
