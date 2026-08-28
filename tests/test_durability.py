"""Durability: what survives a kill, and what two writers do to each other.

`docs/ROADMAP.md` has carried one sentence since the lakehouse landed:

    This is not a claim of full crash durability: concurrent writers, historical warehouse
    migrations, and a full SIGKILL/fsync crash matrix remain open.

That sentence is honest and it is also a gap: nothing measured what actually happens. These
tests measure it. An ingest is run in a real subprocess and killed with SIGKILL at a spread of
offsets across its lifetime; after every kill, the warehouse is opened and required to be in one
of the states the design says are the only possible ones. Two writers are then raced against the
same warehouse and the outcome is asserted rather than assumed.

The invariant under test, in one sentence: **a killed run leaves a warehouse that is either
untouched, recoverable, or complete, and never one that reports a snapshot it does not hold.**
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from datetime import date
from pathlib import Path
from typing import Any

import duckdb
import pytest
from test_lakehouse import _document

from mrf_honest.lakehouse import MANIFEST_SCHEMA_VERSION, PublisherRef, ingest_hospital_file

#: Where in a run the kill lands, named by the artifact whose first appearance marks that the
#: run has reached that stage. Wall-clock offsets were tried first and abandoned: a kill
#: scheduled at a fraction of a run measured a second earlier lands wherever the machine's load
#: happens to put it, so the same offset interrupted a different stage on every run and
#: sometimes none at all. A progress marker is the same point on a fast machine and a slow one.
KILL_POINTS: tuple[tuple[str, str], ...] = (
    ("database opened", "warehouse.duckdb"),
    ("source snapshotted", ".staging/*/sources/sha256/*/*.json"),
    ("spool written", ".staging/*/spool/*.tsv"),
    ("models loaded", ".staging/*/profiles/load-*.json"),
    ("marts built", ".staging/*/profiles/build-*.json"),
    ("parquet staged", ".staging/*/parquet/**/*.parquet"),
)

#: How long to wait for a marker before concluding the run passed that stage without this
#: watcher seeing it, or finished first.
MARKER_TIMEOUT_SECONDS = 60.0

_RUNNER = """
import json, sys
from datetime import date
from pathlib import Path
from mrf_honest.lakehouse import PublisherRef, ingest_hospital_file

result = ingest_hospital_file(
    Path(sys.argv[1]),
    Path(sys.argv[2]),
    publisher=PublisherRef("example-health"),
    as_of=date(2026, 4, 1),
)
print(json.dumps({"run_id": result.run_id}))
"""


def _source(tmp_path: Path) -> Path:
    """A real CMS-shaped document, big enough that a run takes long enough to interrupt."""

    document = _document()
    charges = list(document["standard_charge_information"])  # type: ignore[arg-type]
    document["standard_charge_information"] = charges * 1200
    path = tmp_path / "standardcharges.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def _runner(tmp_path: Path) -> Path:
    path = tmp_path / "run_ingest.py"
    path.write_text(_RUNNER, encoding="utf-8")
    return path


def _spawn(runner: Path, source: Path, warehouse: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [sys.executable, str(runner), str(source), str(warehouse)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent / "src")},
    )


def _manifests(warehouse: Path) -> list[dict[str, Any]]:
    """Run manifests as the lakehouse writes them: `runs/<run_id>.json`, one per run."""

    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted((warehouse / "runs").glob("*.json"))
    ]


def test_the_manifest_reader_finds_a_real_manifest(tmp_path: Path) -> None:
    """A reader that silently finds nothing would make every crash assertion vacuous, and did:
    the first version of this file globbed for `manifest.json` and found none, which made the
    catalog look as though it claimed runs with no manifest behind them."""

    source = _source(tmp_path)
    warehouse = tmp_path / "warehouse"
    result = ingest_hospital_file(
        source, warehouse, publisher=PublisherRef("example-health"), as_of=date(2026, 4, 1)
    )
    manifests = _manifests(warehouse)
    assert [manifest["run_id"] for manifest in manifests] == [result.run_id]
    assert manifests[0]["status"] == "success"


#: The catalog states a run is running, succeeded, or failed. Anything else would be a status
#: this suite has never seen, and reading it as though it were one of these would be a guess.
CATALOG_STATUSES = frozenset({"running", "success", "failed"})


def _catalog(warehouse: Path) -> list[tuple[str, str]]:
    """Every catalog row and its status, read without writing anything."""

    database = warehouse / "warehouse.duckdb"
    if not database.is_file():
        return []
    try:
        with duckdb.connect(str(database), read_only=True) as connection:
            rows = connection.execute("SELECT run_id, status FROM ingest_run").fetchall()
    except duckdb.Error:
        # A database that cannot even be opened read-only is a state this test must be able to
        # report, not one it should hide behind an exception.
        return [("<unopenable>", "<unopenable>")]
    return [(str(row[0]), str(row[1])) for row in rows]


def _catalog_runs(warehouse: Path) -> list[str]:
    """Run identifiers the catalog reports as complete."""

    return [run_id for run_id, status in _catalog(warehouse) if status == "success"]


def _wait_for(warehouse: Path, pattern: str, process: subprocess.Popen[str]) -> bool:
    """Block until the marker appears, the run ends, or the timeout expires.

    Returns whether the marker was seen. A run that finishes first is not a failure of the
    subject; it is a sample that did not interrupt anything, and the sweep counts it as such.
    """

    deadline = time.monotonic() + MARKER_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        if warehouse.is_dir() and any(warehouse.glob(pattern)):
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.002)
    return False


class TestCrashMatrix:
    """One test per claim, each sweeping every offset, rather than one test per offset.

    A per-offset test cannot know whether the sweep as a whole proved anything. A kill scheduled
    at a fraction of a measured run sometimes lands after the run has already finished, because
    the machine is not the machine the baseline was measured on a second earlier. A per-offset
    test can only fail on that, or skip, and a suite where every sample quietly skipped would be
    green having tested nothing. Sweeping inside one test lets it assert the thing that actually
    matters: **enough samples were genuinely killed for the sweep to mean something.**
    """

    #: Below this many genuine kills out of `KILL_POINTS`, the sweep proved nothing and says so.
    MINIMUM_GENUINE_KILLS = 4

    def _sweep(self, tmp_path: Path) -> list[tuple[str, bool, Path]]:
        """Run the matrix once, returning per stage whether the kill actually landed."""

        source = _source(tmp_path)
        runner = _runner(tmp_path)
        outcomes: list[tuple[str, bool, Path]] = []
        for stage, pattern in KILL_POINTS:
            warehouse = tmp_path / f"warehouse-{stage.replace(' ', '-')}"
            process = _spawn(runner, source, warehouse)
            if _wait_for(warehouse, pattern, process):
                process.send_signal(signal.SIGKILL)
            process.wait(timeout=120)
            outcomes.append((stage, process.returncode == -signal.SIGKILL, warehouse))
        return outcomes

    def test_a_sigkill_leaves_the_warehouse_in_a_stated_state(self, tmp_path: Path) -> None:
        """Untouched, recoverable, or complete. Never a success the artifacts do not back."""

        outcomes = self._sweep(tmp_path)
        killed = [entry for entry in outcomes if entry[1]]
        assert len(killed) >= self.MINIMUM_GENUINE_KILLS, (
            f"only {len(killed)} of {len(outcomes)} samples were genuinely killed; the sweep "
            "did not exercise enough interruption points to mean anything"
        )
        for stage, _, warehouse in outcomes:
            manifests = _manifests(warehouse)
            catalog = _catalog(warehouse)
            if catalog == [("<unopenable>", "<unopenable>")]:
                # Measured, and kept rather than asserted away. Killing at the instant DuckDB
                # first creates `warehouse.duckdb` leaves a file it will not open read-only: the
                # file exists before its header does. That is not a false claim, which is what
                # this suite guards against, and it is not permanent either. The invariant is
                # that a re-run recovers it, and `test_a_killed_run_can_be_re_run_to_completion`
                # covers every stage including this one.
                assert stage == "database opened", (
                    f"an unopenable warehouse after a kill at {stage!r}, which is past the "
                    "window where the database file exists without a header"
                )
                assert not manifests, "an unopenable database with a manifest beside it"
                continue
            for _run_id, status in catalog:
                assert status in CATALOG_STATUSES, f"unrecognised catalog status {status!r}"
            for manifest in manifests:
                assert manifest["schema_version"] == MANIFEST_SCHEMA_VERSION
                assert manifest["status"] in {"prepared", "success"}

            # The load-bearing invariant. A `running` row is a recoverable interruption and
            # legitimately has no manifest yet. A `success` row is a claim that a snapshot
            # exists, and that claim must be backed by a manifest and its Parquet artifacts.
            manifest_runs = {str(manifest["run_id"]) for manifest in manifests}
            claimed = {run_id for run_id, status in catalog if status == "success"}
            assert claimed <= manifest_runs, (
                f"at the {stage!r} point, the catalog reports success for "
                f"{sorted(claimed - manifest_runs)} with no manifest"
            )
            for manifest in manifests:
                if manifest["status"] != "success":
                    continue
                for artifact in manifest["artifacts"]:
                    assert (warehouse / artifact["path"]).is_file(), (
                        f"at the {stage!r} point, a successful manifest names {artifact['path']}, "
                        "which is not on disk"
                    )

    def test_a_killed_run_is_never_reported_as_a_completed_one(self, tmp_path: Path) -> None:
        """The fail-closed half. An interrupted run may leave a `running` row; it may never
        leave a `success` one, because a success row is what `query_file_profile` reads.

        Samples where the run finished before the kill landed are not interruptions and are not
        judged as such; they are counted, and the sweep fails if too few were real.
        """

        outcomes = self._sweep(tmp_path)
        killed = [entry for entry in outcomes if entry[1]]
        assert len(killed) >= self.MINIMUM_GENUINE_KILLS, (
            f"only {len(killed)} of {len(outcomes)} samples were genuinely killed"
        )
        for stage, _, warehouse in killed:
            if _catalog(warehouse) == [("<unopenable>", "<unopenable>")]:
                continue  # no catalog to read yet; covered by the recovery test
            assert _catalog_runs(warehouse) == [], (
                f"a run killed at {stage!r} is reported by the catalog as a completed snapshot"
            )

    def test_a_killed_run_can_be_re_run_to_completion(self, tmp_path: Path) -> None:
        """Recovery is the point of a prepared state. A killed warehouse must not be a dead one."""

        source = _source(tmp_path)
        outcomes = self._sweep(tmp_path)
        assert sum(1 for entry in outcomes if entry[1]) >= self.MINIMUM_GENUINE_KILLS
        for stage, _, warehouse in outcomes:
            result = ingest_hospital_file(
                source,
                warehouse,
                publisher=PublisherRef("example-health"),
                as_of=date(2026, 4, 1),
            )
            assert result.run_id, (
                f"no run id after re-running a warehouse killed at the {stage!r} point"
            )
            assert result.database_path.is_file()
            assert _catalog_runs(warehouse) == [result.run_id]

    def test_the_matrix_covers_more_than_one_stage(self) -> None:
        """A one-point matrix would be a single test wearing a table's clothes."""

        assert len(KILL_POINTS) >= 5
        assert len({pattern for _, pattern in KILL_POINTS}) == len(KILL_POINTS)


class TestConcurrentWriters:
    def test_two_writers_never_leave_two_snapshots_of_one_source(self, tmp_path: Path) -> None:
        """DuckDB holds a single-writer lock on the database file. The measured consequence is
        that one process wins and the other fails to acquire it; what must never happen is two
        snapshots of one source, or a catalog row whose artifacts another writer removed."""

        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        runner = _runner(tmp_path)
        first = _spawn(runner, source, warehouse)
        second = _spawn(runner, source, warehouse)
        first_out, first_err = first.communicate(timeout=180)
        second_out, second_err = second.communicate(timeout=180)

        succeeded = [
            out
            for code, out in ((first.returncode, first_out), (second.returncode, second_out))
            if code == 0
        ]
        assert succeeded, (
            "neither writer completed; stderr was "
            f"{first_err.strip()[-300:]!r} and {second_err.strip()[-300:]!r}"
        )
        runs = _catalog_runs(warehouse)
        assert len(set(runs)) == 1, f"one source produced {len(set(runs))} catalog runs: {runs}"
        for output in succeeded:
            assert json.loads(output)["run_id"] == runs[0]

    def test_a_loser_fails_loudly_rather_than_writing_anyway(self, tmp_path: Path) -> None:
        """A writer that could not take the lock must say so, not return a quiet success."""

        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        runner = _runner(tmp_path)
        first = _spawn(runner, source, warehouse)
        second = _spawn(runner, source, warehouse)
        outcomes = [first.communicate(timeout=180), second.communicate(timeout=180)]
        codes = [first.returncode, second.returncode]
        for code, (out, err) in zip(codes, outcomes, strict=True):
            if code == 0:
                assert json.loads(out)["run_id"]
            else:
                assert err.strip(), "a failed writer exited without saying why"

    def test_a_second_run_after_a_completed_one_reuses_rather_than_duplicating(
        self, tmp_path: Path
    ) -> None:
        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        as_of = date(2026, 4, 1)
        first = ingest_hospital_file(
            source, warehouse, publisher=PublisherRef("example-health"), as_of=as_of
        )
        second = ingest_hospital_file(
            source, warehouse, publisher=PublisherRef("example-health"), as_of=as_of
        )
        assert first.run_id == second.run_id
        assert _catalog_runs(warehouse) == [first.run_id]


class TestDeterministicFaults:
    """What a sampled kill cannot show.

    The SIGKILL matrix above is evidence, not proof: it samples seven offsets, so a window that
    is microseconds wide is missed unless a kill happens to land in it. That was measured, not
    assumed. Reordering the catalog commit ahead of artifact promotion left the whole matrix
    green, because the window between them is too narrow for a sampled kill to find.

    These tests close that gap by failing at a chosen point rather than a chosen time. Each one
    injects a failure at one stage and asserts the same invariant: **the catalog never reports a
    success whose artifacts are not on disk.**
    """

    def _fail_at(self, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
        import mrf_honest.lakehouse as module

        def explode(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise OSError(f"injected failure in {name}")

        monkeypatch.setattr(module, name, explode)

    def test_a_failure_during_promotion_leaves_no_successful_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The ordering claim, tested at the point rather than at a moment: promotion happens
        before the catalog commit, so a promotion that fails cannot leave a success behind."""

        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        self._fail_at(monkeypatch, "_promote")
        with pytest.raises((OSError, Exception)):
            ingest_hospital_file(
                source, warehouse, publisher=PublisherRef("example-health"), as_of=date(2026, 4, 1)
            )
        assert _catalog_runs(warehouse) == [], (
            "promotion failed and the catalog still reports a completed snapshot"
        )

    def test_a_failure_during_the_parquet_write_leaves_no_successful_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        self._fail_at(monkeypatch, "_write_parquet")
        with pytest.raises(Exception):  # noqa: B017 - the type is the injected one
            ingest_hospital_file(
                source, warehouse, publisher=PublisherRef("example-health"), as_of=date(2026, 4, 1)
            )
        assert _catalog_runs(warehouse) == []

    def test_a_failure_during_the_manifest_write_leaves_no_successful_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        self._fail_at(monkeypatch, "_write_manifest")
        with pytest.raises(Exception):  # noqa: B017 - the type is the injected one
            ingest_hospital_file(
                source, warehouse, publisher=PublisherRef("example-health"), as_of=date(2026, 4, 1)
            )
        assert _catalog_runs(warehouse) == []

    @pytest.mark.parametrize("stage", ["_promote", "_write_parquet", "_write_manifest"])
    def test_the_warehouse_recovers_after_any_injected_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
    ) -> None:
        """A failed run must leave a warehouse a later run can finish, not a poisoned one."""

        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        with monkeypatch.context() as patched:
            self._fail_at(patched, stage)
            with pytest.raises(Exception):  # noqa: B017 - the type is the injected one
                ingest_hospital_file(
                    source,
                    warehouse,
                    publisher=PublisherRef("example-health"),
                    as_of=date(2026, 4, 1),
                )
        result = ingest_hospital_file(
            source, warehouse, publisher=PublisherRef("example-health"), as_of=date(2026, 4, 1)
        )
        assert _catalog_runs(warehouse) == [result.run_id]
        manifests = [m for m in _manifests(warehouse) if m["status"] == "success"]
        assert [m["run_id"] for m in manifests] == [result.run_id]

    def test_a_recovered_warehouse_holds_no_orphan_artifacts(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every Parquet under the warehouse belongs to a run a successful manifest names."""

        source = _source(tmp_path)
        warehouse = tmp_path / "warehouse"
        with monkeypatch.context() as patched:
            self._fail_at(patched, "_promote")
            with pytest.raises(Exception):  # noqa: B017 - the type is the injected one
                ingest_hospital_file(
                    source,
                    warehouse,
                    publisher=PublisherRef("example-health"),
                    as_of=date(2026, 4, 1),
                )
        result = ingest_hospital_file(
            source, warehouse, publisher=PublisherRef("example-health"), as_of=date(2026, 4, 1)
        )
        named = {
            str(artifact["path"])
            for manifest in _manifests(warehouse)
            if manifest["status"] == "success"
            for artifact in manifest["artifacts"]
        }
        on_disk = {
            str(path.relative_to(warehouse))
            for path in warehouse.rglob("*.parquet")
            if ".staging" not in path.parts
        }
        assert on_disk <= named, f"orphan artifacts: {sorted(on_disk - named)}"
        assert result.run_id


def test_one_window_this_suite_does_not_reach() -> None:
    """Stated rather than implied, because an untested path that looks tested is worse than one
    that is named.

    `_clean_promoted` removes artifacts promoted by a run whose catalog commit then failed. That
    window is a single SQL statement wide, between `_promote` and `COMMIT`. No stage these tests
    can fail lands inside it, and a mutant that makes `_clean_promoted` a no-op leaves this suite
    green. Reaching it needs a fault injected into the database driver rather than into this
    module, which is a different tool than the one this suite is built on.

    fsync behaviour is unmeasured here for the same reason: it needs a filesystem-level fault
    injector. `docs/ROADMAP.md` says so rather than letting "crash matrix" imply it.
    """

    import mrf_honest.lakehouse as module

    assert callable(module._clean_promoted)
