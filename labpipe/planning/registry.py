from labpipe.planning.format import encode_plan, decode_plan, PlanError


class PlanRegistry:
    def __init__(self, storage):
        self.storage = storage

    def save_plan(self, document):
        from labpipe.execution.runner import validate_executable
        independent = decode_plan(encode_plan(document))
        validate_executable(independent)
        with self.storage.transaction() as db:
            return db.execute('INSERT INTO lp_plans(doc) VALUES(?)',
                              (encode_plan(independent),)).lastrowid

    def load_plan(self, plan_id):
        row = self.storage.connection.execute(
            'SELECT doc FROM lp_plans WHERE id=?', (plan_id,)
        ).fetchone()
        if row is None:
            raise PlanError('unknown plan: ' + str(plan_id))
        return decode_plan(row[0])

    def get_metadata(self, plan_id):
        document = self.load_plan(plan_id)
        return {'plan_id': plan_id, 'snapshot_id': document.snapshot_id,
                'format_version': document.version, 'output_types': dict(document.output_types)}
