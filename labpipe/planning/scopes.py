from labpipe.planning.format import PlanError


class ScopeResolver:
    def __init__(self):
        self.frames = []
        self.next_binding = 1

    def declare(self, alias, columns):
        if type(alias) is not str or not alias.strip():
            raise PlanError('declaration requires a nonempty alias')
        declaration = {'alias': alias, 'binding': self.next_binding, 'types': dict(columns)}
        self.next_binding += 1
        return declaration

    def enter_scope(self, declaration):
        self.frames.append({declaration['alias']: declaration})

    def leave_scope(self):
        if not self.frames:
            raise PlanError('unbalanced lexical scope exit')
        self.frames.pop()

    def resolve(self, name, alias=None, up=0):
        if type(name) is not str or type(up) is not int or up < 0 or up >= len(self.frames):
            raise PlanError('invalid column reference or lexical depth')
        frames = list(reversed(self.frames))[up:]
        if alias is not None:
            if type(alias) is not str:
                raise PlanError('invalid reference alias')
            for frame in frames:
                if alias in frame:
                    declaration = frame[alias]
                    if name not in declaration['types']:
                        raise PlanError('unknown column: ' + alias + '.' + name)
                    return declaration['binding'], declaration['types'][name]
            raise PlanError('unknown alias: ' + alias)
        visible = {}
        for frame in frames:
            for key, declaration in frame.items():
                visible.setdefault(key, declaration)
        candidates = [d for d in visible.values() if name in d['types']]
        if len(candidates) != 1:
            raise PlanError('unknown or ambiguous unqualified column: ' + name)
        declaration = candidates[0]
        return declaration['binding'], declaration['types'][name]
