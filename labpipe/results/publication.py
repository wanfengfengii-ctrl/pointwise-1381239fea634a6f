from labpipe.results.codec import encode_row, ResultError
from labpipe.results.provenance import build_provenance, encode_provenance
from labpipe.planning.registry import PlanRegistry


def publish_result(storage, plan_id, rows):
    document = PlanRegistry(storage).load_plan(plan_id)
    expected = set(document.output_types)
    encoded = []
    for row in rows:
        if type(row) is not dict or set(row) != expected:
            raise ResultError('result does not match plan output columns')
        for key, kind in document.output_types.items():
            value = row[key]
            allowed_null = kind in ('null', 'int?')
            if value is None:
                if not allowed_null:
                    raise ResultError('null in nonnullable result column')
            elif kind in ('int', 'int?'):
                if type(value) is not int:
                    raise ResultError('noninteger result value')
            elif kind != 'str' or type(value) is not str:
                raise ResultError('result value has wrong declared type')
        encoded.append(encode_row(row))
    with storage.transaction() as db:
        provenance = build_provenance(storage, plan_id, len(encoded))
        result_id = db.execute(
            'INSERT INTO lp_results(plan_id,provenance) VALUES(?,NULL)', (plan_id,)
        ).lastrowid
        for ordinal, row_json in enumerate(encoded):
            db.execute('INSERT INTO lp_result_rows VALUES(?,?,?)', (result_id, ordinal, row_json))
        db.execute('UPDATE lp_results SET provenance=? WHERE id=?',
                   (encode_provenance(provenance), result_id))
        return result_id
