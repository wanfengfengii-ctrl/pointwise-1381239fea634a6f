from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    kind: str
    nullable: bool = False

    @property
    def type_name(self):
        return self.kind + ('?' if self.nullable else '')


NORMALIZED_UNIT = 'U'
TABLE_SCHEMAS = {
    table: (
        ColumnSpec('batch_id', 'str'),
        ColumnSpec('row_id', 'str'),
        ColumnSpec('station', 'str'),
        ColumnSpec('value', 'int', True),
    )
    for table in ('observations', 'calibrations')
}


def get_schema(table):
    if table not in TABLE_SCHEMAS:
        raise ValueError('unknown source table: ' + str(table))
    return {c.name: c.type_name for c in TABLE_SCHEMAS[table]}


def is_valid_scalar(value):
    return value is None or type(value) in (str, int)
