"""Public fixtures and hand-tally checks; no audited executor is supplied."""
import argparse
import copy
import json
import sqlite3
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch
from labpipe.catalog.storage import Storage
from labpipe.catalog.snapshots import freeze, get_snapshot
from labpipe.workflow.intake import ingest
from labpipe.workflow.processing import execute
from labpipe.planning.compiler import compile
from labpipe.results.reader import read
from labpipe.results.codec import ResultError
from tests.test_intake import quality_query, col, scan, eq
from tests.test_plans import nested_query


def fixtures():
    obs = [dict(row_id="o" + str(i), station=s, value=v)
           for i, (s, v) in enumerate([("A", 10), ("A", 10), ("B", 20),
                                       ("C", 30), ("D", 40)], 1)]
    cal = [dict(row_id=k, station=s, value=v) for k, s, v in
           [("c1", "A", 3), ("c2", "A", 3), ("cn", "A", None),
            ("cc", "C", None), ("d1", "D", 3), ("d2", "D", -3)]]
    expected = [dict(station=s, count=n, sum=v) for s, n, v in
                [("A", 3, 6), ("A", 3, 6), ("B", 0, None),
                 ("C", 1, None), ("D", 2, 0)]]
    return obs, cal, expected


def report_query():
    query = quality_query()
    del query["columns"]["row_id"]
    return query


def wrapped_query():
    return {"op": "project", "input": report_query(), "as": "hidden",
            "columns": {"count": col("report", "count"), "sum": col("report", "sum")}}


def inner_count():
    return {"op": "count", "query": {
        "op": "filter", "input": scan("observations", "m"),
        "where": eq(col("m", "station"), col("c", "station"))}}


def rejection_query():
    return {"op": "project", "input": scan("observations", "s"), "as": "r",
            "columns": {"n": {"op": "count", "query": {
                "op": "filter", "input": scan("calibrations", "c"),
                "where": eq(inner_count(), {"op": "lit", "value": 2})}}}}


def member_value_query():
    return {"op": "project", "input": scan("observations", "s"), "as": "r",
            "columns": {"total": {"op": "sum", "query": scan("calibrations", "c"),
                                   "value": inner_count()}}}


def seed(storage):
    obs, cal, _ = fixtures()
    o = ingest(storage, "observations", "day", obs)
    c = ingest(storage, "calibrations", "bench", cal)
    return freeze(storage, o["revision_id"], c["revision_id"])


def v1_check():
    with Storage.open() as storage:
        fixed = seed(storage)
        expected = fixtures()[2]
        cases = [(report_query(), expected),
                 (wrapped_query(), [{k: row[k] for k in ("count", "sum")} for row in expected]),
                 (rejection_query(), [{"n": 3}] * 5),
                 (member_value_query(), [{"total": 9}] * 5)]
        for query, wanted in cases:
            actual = read(storage, execute(storage, compile(storage, fixed, query)))["rows"]
            assert actual == wanted, (wanted, actual)
        for query in (nested_query(), nested_query("outer", "inner")):
            wanted = [dict(row, row_id="o" + str(i), after=row["station"])
                      for i, row in enumerate(expected, 1)]
            assert read(storage, execute(storage, compile(storage, fixed, query)))["rows"] == wanted
    print("V1_FIXTURES_OK: duplicate=3/6, absent=0/null, all-null=1/null, zero=2/0; nested=3,9")


def source_key(source):
    return source["table"], source["batch_id"], source["row_id"]


def check_source(source, revisions):
    assert set(source) == {"revision_id", "table", "batch_id", "row_id"}
    assert source["revision_id"] == revisions[source["table"]]
    assert all(type(source[k]) is str and source[k].strip()
               for k in ("table", "batch_id", "row_id"))


def check_call(call, revisions):
    assert set(call) == {"node", "op", "result", "candidates", "members"}
    assert isinstance(call["node"], list) and all(type(k) is str for k in call["node"])
    assert call["op"] in ("count", "sum")
    candidates = call["candidates"]
    assert [x["ordinal"] for x in candidates] == list(range(len(candidates)))
    for candidate in candidates:
        assert set(candidate) == {"ordinal", "source", "accepted", "calls"}
        check_source(candidate["source"], revisions)
        assert type(candidate["accepted"]) is bool
        for nested in candidate["calls"]:
            check_call(nested, revisions)
    assert [m["candidate"] for m in call["members"]] == [
        x["ordinal"] for x in candidates if x["accepted"]]
    values = []
    for member in call["members"]:
        assert set(member) == {"candidate", "value", "calls"}
        assert member["value"] is None or type(member["value"]) is int
        for nested in member["calls"]:
            check_call(nested, revisions)
        if call["op"] == "count":
            assert member["value"] == 1 and member["calls"] == []
        values.append(member["value"])
    wanted = len(values) if call["op"] == "count" else (
        sum(v for v in values if v is not None) if any(v is not None for v in values) else None)
    assert call["result"] == wanted
    assert call["result"] is None or type(call["result"]) is int


def check_document(document, base):
    assert set(document) == set(base) | {"audit"}
    assert {k: document[k] for k in base} == base
    assert len(document["audit"]) == len(base["rows"])
    for ordinal, output in enumerate(document["audit"]):
        assert set(output) == {"ordinal", "source", "calls"}
        assert output["ordinal"] == ordinal
        check_source(output["source"], base["provenance"]["revisions"])
        for call in output["calls"]:
            check_call(call, base["provenance"]["revisions"])


def one_call(output, path):
    calls = [c for c in output["calls"] if c["node"] == path]
    assert len(calls) == 1, (path, output)
    return calls[0]


@contextmanager
def abort_writes(storage, table, event="INSERT"):
    """Use the actual table/event storing evidence; also supports UPDATE of a header."""
    assert table.replace("_", "").isalnum() and event in ("INSERT", "UPDATE")
    assert storage.connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()
    storage.connection.execute('CREATE TRIGGER audit_fixture_abort BEFORE ' + event +
                               ' ON "' + table + '" BEGIN SELECT RAISE(ABORT,'
                               "'audit evidence write rejected'); END")
    try:
        yield
    finally:
        storage.connection.execute("DROP TRIGGER audit_fixture_abort")


def database_counts(storage):
    names = [r[0] for r in storage.connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    return {n: storage.connection.execute('SELECT count(*) FROM "' + n + '"').fetchone()[0]
            for n in names}


def audited_check(evidence_table=None, event="INSERT"):
    from labpipe.workflow.processing import execute_audited
    from labpipe.results.reader import read_audit
    from labpipe.results.export import export_audit
    with Storage.open() as storage:
        fixed = seed(storage)
        expected = fixtures()[2]
        last_id = None
        for query, wanted in [
            (report_query(), expected),
            (wrapped_query(), [{k: r[k] for k in ("count", "sum")} for r in expected]),
            (rejection_query(), [{"n": 3}] * 5),
            (member_value_query(), [{"total": 9}] * 5),
            (nested_query(), [dict(r, row_id="o" + str(i), after=r["station"])
                              for i, r in enumerate(expected, 1)]),
            (nested_query("outer", "inner"), [dict(r, row_id="o" + str(i), after=r["station"])
                                             for i, r in enumerate(expected, 1)])]:
            plan = compile(storage, fixed, query)
            ordinary = execute(storage, plan)
            with _raises(ResultError):
                read_audit(storage, ordinary)
            result = execute_audited(storage, plan)
            base = read(storage, result)
            assert base["rows"] == wanted == read(storage, ordinary)["rows"]
            document = read_audit(storage, result)
            check_document(document, base)
            assert [source_key(o["source"]) for o in document["audit"]] == [
                ("observations", "day", "o" + str(i)) for i in range(1, 6)]
            if query == report_query() or query == wrapped_query():
                prefix = [] if query == report_query() else ["input"]
                obs, cal, _ = fixtures()
                for output, row in zip(document["audit"], obs):
                    for op in ("count", "sum"):
                        call = one_call(output, prefix + ["columns", op])
                        members = call["members"]
                        keys = [source_key(call["candidates"][m["candidate"]]["source"])
                                for m in members]
                        matches = [c for c in cal if c["station"] == row["station"]]
                        assert keys == [("calibrations", "bench", c["row_id"]) for c in matches]
                        assert [m["value"] for m in members] == [
                            1 if op == "count" else c["value"] for c in matches]
            if query == rejection_query():
                for output in document["audit"]:
                    call = one_call(output, ["columns", "n"])
                    assert [c["accepted"] for c in call["candidates"]] == [True] * 3 + [False] * 3
                    assert [c["calls"][0]["result"] for c in call["candidates"]] == [2] * 3 + [1] * 3
                    assert all(len(c["calls"]) == 1 for c in call["candidates"])
                    assert all(c["calls"][0]["node"] == [
                        "columns", "n", "query", "where", "left"] for c in call["candidates"])
            if query == member_value_query():
                for output in document["audit"]:
                    call = one_call(output, ["columns", "total"])
                    assert all(c["calls"] == [] for c in call["candidates"])
                    assert [m["value"] for m in call["members"]] == [2] * 3 + [1] * 3
                    assert all(len(m["calls"]) == 1 and m["calls"][0]["node"] ==
                               ["columns", "total", "value"] for m in call["members"])
            saved = copy.deepcopy(document)
            document["audit"].clear()
            assert read_audit(storage, result) == saved
            last_id = result
        # Post-projection filtering must carry the earlier calls and hidden source.
        filtered = {"op": "filter", "input": wrapped_query(),
                    "where": eq(col("hidden", "count"), {"op": "lit", "value": 3})}
        filtered_id = execute_audited(storage, compile(storage, fixed, filtered))
        filtered_doc = read_audit(storage, filtered_id)
        check_document(filtered_doc, read(storage, filtered_id))
        assert [o["source"]["row_id"] for o in filtered_doc["audit"]] == ["o1", "o2"]
        assert all(len(o["calls"]) == 2 for o in filtered_doc["audit"])
        # Same row_id in another batch is another member; integers stay exact.
        extra = ingest(storage, "calibrations", "other",
                       [{"row_id": "c1", "station": "A", "value": 2 ** 70}])
        obs_revision = get_snapshot(storage, fixed)["tables"]["observations"]["revision_id"]
        extra_fixed = freeze(storage, obs_revision, extra["revision_id"])
        extra_id = execute_audited(storage, compile(storage, extra_fixed, report_query()))
        extra_doc = read_audit(storage, extra_id)
        check_document(extra_doc, read(storage, extra_id))
        assert extra_doc["rows"][0] == {"station": "A", "count": 4, "sum": 2 ** 70 + 6}
        extra_sum = one_call(extra_doc["audit"][0], ["columns", "sum"])
        assert [source_key(extra_sum["candidates"][m["candidate"]]["source"])
                for m in extra_sum["members"]] == [
                    ("calibrations", "bench", "c1"), ("calibrations", "bench", "c2"),
                    ("calibrations", "bench", "cn"), ("calibrations", "other", "c1")]
        # Fixed old plan, source correction, then true disk reopen and file export.
        ingest(storage, "calibrations", "bench",
               [{"row_id": "replacement", "station": "A", "value": 999}])
        if evidence_table:
            before = database_counts(storage)
            with abort_writes(storage, evidence_table, event):
                with _raises(sqlite3.IntegrityError):
                    execute_audited(storage, plan)
            assert database_counts(storage) == before
            assert read_audit(storage, last_id) == saved
        with tempfile.TemporaryDirectory(prefix="labpipe-audit-") as directory:
            path = Path(directory) / "archive.sqlite"
            with Storage.open(path) as disk:
                storage.connection.backup(disk.connection)
            with Storage.open(path) as reopened:
                with patch("labpipe.execution.runner.run_plan", side_effect=AssertionError("reexecuted")), \
                     patch("labpipe.catalog.relations.SnapshotRelationReader.scan",
                           side_effect=AssertionError("rescanned")):
                    assert read_audit(reopened, last_id) == saved
                    target = Path(directory) / "evidence.json"
                    assert json.loads(export_audit(reopened, last_id, target)) == saved
                    assert json.loads(target.read_text(encoding="utf-8")) == saved
        # Empty audited output must remain distinguishable from a legacy result.
        empty = {"op": "filter", "input": scan("observations", "x"),
                 "where": eq(col("x", "station"), {"op": "lit", "value": "missing"})}
        rid = execute_audited(storage, compile(storage, fixed, empty))
        assert read_audit(storage, rid)["audit"] == []
    print("POINTWISE_AUDIT " + json.dumps({
        "duplicate_rows": expected[:2], "absence_null_zero": expected[2:],
        "rejected_candidate_inner_counts": [2, 2, 2, 1, 1, 1],
        "nested_member_total": 9, "reopened_export_equal": True,
        "write_abort_checked": bool(evidence_table)}, ensure_ascii=False))


@contextmanager
def _raises(error):
    try:
        yield
    except error:
        return
    raise AssertionError("expected " + error.__name__)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("v1", "check"))
    parser.add_argument("--evidence-table")
    parser.add_argument("--evidence-event", choices=("INSERT", "UPDATE"), default="INSERT")
    args = parser.parse_args()
    if args.mode == "v1":
        v1_check()
    else:
        audited_check(args.evidence_table, args.evidence_event)
