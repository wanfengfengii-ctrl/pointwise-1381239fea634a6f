from labpipe.planning.format import PlanError


def count_rows(rows):
    count = 0
    for _ in rows:
        count += 1
    return count


def sum_values(values):
    total = 0
    seen = False
    for value in values:
        if value is None:
            continue
        if type(value) is not int:
            raise PlanError('noninteger value reached integer aggregate')
        seen = True
        total += value
    return total if seen else None
