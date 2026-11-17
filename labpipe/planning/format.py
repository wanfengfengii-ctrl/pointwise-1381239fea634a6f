import json
from dataclasses import dataclass
from labpipe.catalog.schemas import is_valid_scalar

PLAN_FORMAT_VERSION = 1


class PlanError(ValueError):
    pass


def freeze_query(query):
    def check(value, active):
        if is_valid_scalar(value):
            return
        if type(value) not in (list, dict):
            raise PlanError('query must contain only JSON objects, lists and business scalars')
        if id(value) in active:
            raise PlanError('cyclic query')
        active.add(id(value))
        if type(value) is dict:
            if any(type(key) is not str for key in value):
                raise PlanError('query keys must be strings')
            children = value.values()
        else:
            children = value
        for child in children:
            check(child, active)
        active.remove(id(value))
    try:
        check(query, set())
        return json.loads(json.dumps(query, ensure_ascii=False))
    except (ValueError, TypeError, RecursionError) as exc:
        raise PlanError('invalid query value: ' + str(exc)) from exc


@dataclass(frozen=True)
class PlanDocument:
    snapshot_id: int
    query: dict
    root: dict
    output_types: dict
    version: int = PLAN_FORMAT_VERSION

    def to_dict(self):
        return {'version': self.version, 'snapshot_id': self.snapshot_id,
                'query': self.query, 'root': self.root, 'output_types': self.output_types}


def encode_plan(document):
    data = document.to_dict() if isinstance(document, PlanDocument) else document
    return json.dumps(freeze_query(data), ensure_ascii=False, separators=(',', ':'))


def decode_plan(encoded):
    try:
        data = json.loads(encoded)
        if type(data) is not dict or set(data) != {
            'version', 'snapshot_id', 'query', 'root', 'output_types'
        }:
            raise PlanError('incomplete plan document')
        if type(data['version']) is not int or data['version'] != PLAN_FORMAT_VERSION:
            raise PlanError('unsupported plan format')
        if type(data['snapshot_id']) is not int:
            raise PlanError('invalid snapshot identity')
        if any(type(data[key]) is not dict for key in ('query', 'root', 'output_types')):
            raise PlanError('invalid plan structure')
        return PlanDocument(**data)
    except (ValueError, TypeError, KeyError) as exc:
        raise PlanError('invalid persisted plan: ' + str(exc)) from exc
