import json
from dataclasses import dataclass, asdict
from labpipe.planning.registry import PlanRegistry
from labpipe.catalog.snapshots import get_snapshot
from labpipe.results.codec import ResultError


@dataclass(frozen=True)
class ResultProvenance:
    plan_id: int
    snapshot_id: int
    revisions: dict
    units: dict
    row_count: int


def build_provenance(storage, plan_id, row_count):
    metadata = PlanRegistry(storage).get_metadata(plan_id)
    snapshot = get_snapshot(storage, metadata['snapshot_id'])
    return ResultProvenance(
        plan_id, snapshot['snapshot_id'],
        {table: item['revision_id'] for table, item in snapshot['tables'].items()},
        {table: item['unit'] for table, item in snapshot['tables'].items()},
        row_count,
    )


def encode_provenance(provenance):
    encoded = json.dumps(asdict(provenance), ensure_ascii=False, separators=(',', ':'))
    decode_provenance(encoded)
    return encoded


def decode_provenance(encoded):
    try:
        data = json.loads(encoded)
        if type(data) is not dict or set(data) != {
            'plan_id', 'snapshot_id', 'revisions', 'units', 'row_count'
        }:
            raise ResultError('incomplete result provenance')
        if any(type(data[k]) is not int or data[k] < 0
               for k in ('plan_id', 'snapshot_id', 'row_count')):
            raise ResultError('invalid provenance identity or cardinality')
        for key in ('revisions', 'units'):
            if type(data[key]) is not dict or set(data[key]) != {'observations', 'calibrations'}:
                raise ResultError('incomplete source provenance')
        if any(type(value) is not int or value <= 0 for value in data['revisions'].values()):
            raise ResultError('invalid source revision identity')
        if any(value != 'U' for value in data['units'].values()):
            raise ResultError('invalid saved source units')
        return ResultProvenance(**data)
    except (TypeError, ValueError, KeyError) as exc:
        raise ResultError('invalid saved provenance: ' + str(exc)) from exc
