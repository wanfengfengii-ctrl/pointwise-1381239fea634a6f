"""Logical audit evidence: validation and JSON encoding.

Evidence is stored as an independent JSON document so integers keep their exact
value and every read returns a fresh, caller-independent object graph.
"""
import json
from labpipe.results.codec import ResultError


def _source(value, revisions):
    if type(value) is not dict or set(value) != {'revision_id', 'table', 'batch_id', 'row_id'}:
        raise ResultError('malformed audit source')
    table = value['table']
    if type(table) is not str or table not in revisions:
        raise ResultError('audit source belongs to an unknown table')
    if type(value['revision_id']) is not int or value['revision_id'] != revisions[table]:
        raise ResultError('audit source revision does not match the fixed input')
    for key in ('batch_id', 'row_id'):
        if type(value[key]) is not str or not value[key].strip():
            raise ResultError('audit source identity must be a nonempty string')
    return value


def _integer_or_null(value):
    return value is None or type(value) is int


def _call(call, revisions):
    if type(call) is not dict or set(call) != {
            'node', 'op', 'result', 'candidates', 'members'}:
        raise ResultError('malformed audit call')
    node = call['node']
    if type(node) is not list or any(type(part) is not str for part in node):
        raise ResultError('audit node path must be a list of keys')
    if call['op'] not in ('count', 'sum'):
        raise ResultError('unknown audit aggregate operator')
    if not _integer_or_null(call['result']):
        raise ResultError('audit aggregate result must be integer or null')
    candidates = call['candidates']
    if type(candidates) is not list:
        raise ResultError('malformed audit candidates')
    if [item.get('ordinal') if type(item) is dict else None
            for item in candidates] != list(range(len(candidates))):
        raise ResultError('audit candidate ordinals are not contiguous')
    accepted = []
    for ordinal, candidate in enumerate(candidates):
        if type(candidate) is not dict or set(candidate) != {
                'ordinal', 'source', 'accepted', 'calls'}:
            raise ResultError('malformed audit candidate')
        if candidate['ordinal'] != ordinal:
            raise ResultError('audit candidate ordinal is out of order')
        _source(candidate['source'], revisions)
        if type(candidate['accepted']) is not bool:
            raise ResultError('audit candidate acceptance must be boolean')
        if type(candidate['calls']) is not list:
            raise ResultError('malformed candidate calls')
        for nested in candidate['calls']:
            _call(nested, revisions)
        if candidate['accepted']:
            accepted.append(ordinal)
    members = call['members']
    if type(members) is not list:
        raise ResultError('malformed audit members')
    if [member.get('candidate') if type(member) is dict else None
            for member in members] != accepted:
        raise ResultError('audit members do not match accepted candidates')
    values = []
    for member in members:
        if type(member) is not dict or set(member) != {'candidate', 'value', 'calls'}:
            raise ResultError('malformed audit member')
        if type(member['candidate']) is not int:
            raise ResultError('audit member candidate must be an integer index')
        if not _integer_or_null(member['value']):
            raise ResultError('audit member value must be integer or null')
        if type(member['calls']) is not list:
            raise ResultError('malformed member calls')
        for nested in member['calls']:
            _call(nested, revisions)
        if call['op'] == 'count':
            if member['value'] != 1 or member['calls'] != []:
                raise ResultError('count members must contribute exactly one with no calls')
            values.append(1)
        else:
            values.append(member['value'])
    if call['op'] == 'count':
        expected = len(members)
    else:
        present = [value for value in values if value is not None]
        expected = sum(present) if present else None
    if call['result'] != expected:
        raise ResultError('audit aggregate result disagrees with its members')


def validate_audit(audit, revisions, row_count):
    if type(audit) is not list:
        raise ResultError('audit evidence must be a list')
    if len(audit) != row_count:
        raise ResultError('audit evidence does not cover every saved output')
    for ordinal, output in enumerate(audit):
        if type(output) is not dict or set(output) != {'ordinal', 'source', 'calls'}:
            raise ResultError('malformed audit output')
        if output['ordinal'] != ordinal:
            raise ResultError('audit output ordinals are not contiguous')
        _source(output['source'], revisions)
        if type(output['calls']) is not list:
            raise ResultError('malformed output calls')
        for call in output['calls']:
            _call(call, revisions)
    return audit


def encode_audit(audit):
    try:
        return json.dumps(audit, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResultError('audit evidence is not representable: ' + str(exc)) from exc


def decode_audit(encoded):
    try:
        return json.loads(encoded)
    except (TypeError, ValueError) as exc:
        raise ResultError('invalid saved audit evidence: ' + str(exc)) from exc


def encode_audit_document(document):
    required = {'result_id', 'plan_id', 'snapshot_id', 'row_order', 'rows',
                'provenance', 'audit'}
    if type(document) is not dict or set(document) != required:
        raise ResultError('incomplete audit export document')
    if any(type(document[key]) is not int for key in ('result_id', 'plan_id', 'snapshot_id')):
        raise ResultError('invalid audit export identities')
    if type(document['rows']) is not list or type(document['audit']) is not list:
        raise ResultError('invalid audit export content')
    if document['row_order'] != list(range(len(document['rows']))):
        raise ResultError('invalid saved result order')
    if len(document['audit']) != len(document['rows']):
        raise ResultError('audit export does not cover every output')
    from labpipe.results.codec import _validate_row
    for row in document['rows']:
        _validate_row(row)
    try:
        return json.dumps(document, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ResultError('audit export is not representable: ' + str(exc)) from exc
