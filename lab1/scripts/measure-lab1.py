"""Measure one PostgreSQL leader failover through Envoy in Lab 1."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone

NODES = ("pg1", "pg2")
SERVICES = ("etcd1", "etcd2", "etcd3", *NODES, "envoy")
TABLE = "lab_replication.demo"
SQL_FLAGS = ["-X", "-qAt", "-w", "-v", "ON_ERROR_STOP=1", "-v", "VERBOSITY=verbose"]
PROBE_TIMEOUT = 1.5
WAIT_LIMIT = 90


def call(args, env=None, timeout=30):
    return subprocess.run(
        args, env=env, timeout=timeout, capture_output=True,
        text=True, encoding="utf-8", errors="replace", check=False,
    )


def checked(result):
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout).strip())
    return result.stdout.strip()


def wait_for(description, observe, timeout=WAIT_LIMIT, interval=0.2):
    until = time.perf_counter() + timeout
    while time.perf_counter() < until:
        result = observe()
        if result:
            return result
        time.sleep(interval)
    raise RuntimeError(f"Timeout waiting for {description}")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def window(probes, old_address):
    changed = [p for p in probes if p["result"] not in (None, old_address)]
    if not changed:
        raise RuntimeError("No successful probe on the new leader")
    first_new = changed[0]
    previous = [p for p in probes if p["start_s"] < first_new["start_s"]]
    last_old = next((p for p in reversed(previous) if p["result"] == old_address), None)
    if last_old is None:
        raise RuntimeError("Missing successful probe on the old leader")
    between = [p for p in previous if p["start_s"] > last_old["start_s"]]
    return {
        "first_new_success_from_stop_request_s": first_new["end_s"],
        "first_new_probe_started_s": first_new["start_s"],
        "last_old_probe_started_s": last_old["start_s"],
        "last_old_success_s": last_old["end_s"],
        "gap_between_successful_observations_s": first_new["end_s"] - last_old["end_s"],
        "failed_probes_between_successes": sum(p["result"] is None for p in between),
        "new_address": first_new["result"],
    }


class Lab:
    def __init__(self, compose, password, interval, output):
        self.prefix = ["docker", "compose", "-f", str(compose)]
        self.env = dict(os.environ, PGCLIENTENCODING="UTF8")
        if password:
            self.env["PGPASSWORD"] = password
        else:
            self.env.pop("PGPASSWORD", None)
        self.interval = interval
        self.output = output
        self.run_id = uuid.uuid4()
        self.ids = []
        self.restart = None
        self.containers = {}
        self.report = {
            "run_id": str(self.run_id), "started_utc": utc_now(),
            "target": "127.0.0.1:15432", "requested_probe_interval_s": interval,
            "probe_timeout_s": PROBE_TIMEOUT,
            "clock": "time.perf_counter; probe times relative to the stop command dispatch",
            "method": "Fresh psql process per probe, independent serial lanes; durations "
                      "include process startup, authentication and SQL. Success gaps are "
                      "sampled observations, not exact outage durations.",
        }

    def save(self):
        temporary = self.output.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.report, indent=2), encoding="utf-8")
        temporary.replace(self.output)

    def compose(self, *args, timeout=60):
        return checked(call([*self.prefix, *args], timeout=timeout))

    def envoy(self, sql, timeout=5):
        dsn = ("hostaddr=127.0.0.1 port=15432 dbname=postgres user=postgres "
               "connect_timeout=2 client_encoding=UTF8 application_name=lab1_measure")
        return call(["psql", *SQL_FLAGS, "-d", dsn, "-c", sql],
                    env=self.env, timeout=timeout)

    def direct(self, node, sql, timeout=5):
        return call(["docker", "exec", "--user", "postgres", self.containers[node],
                     "psql", *SQL_FLAGS, "-U", "postgres", "-d", "postgres", "-c", sql],
                    timeout=timeout)

    def ready(self, node, sql, timeout=5):
        try:
            result = self.direct(node, sql, timeout)
        except subprocess.TimeoutExpired:
            return None
        return result.stdout.strip() if result.returncode == 0 else None

    def running(self, service):
        result = checked(call([
            "docker", "inspect", "--format", "{{.State.Running}}", self.containers[service]
        ]))
        return result == "true"

    def sync_pair(self):
        roles = {node: self.ready(node, "SELECT pg_is_in_recovery()") for node in NODES}
        if sorted(str(role) for role in roles.values()) != ["f", "t"]:
            return None
        leader = next(node for node, role in roles.items() if role == "f")
        standby = next(node for node, role in roles.items() if role == "t")
        query = (
            "SELECT EXISTS (SELECT 1 FROM pg_stat_replication "
            f"WHERE application_name = '{standby}' AND state = 'streaming' "
            "AND sync_state IN ('sync', 'quorum') AND replay_lsn >= flush_lsn)"
        )
        return (leader, standby) if self.ready(leader, query) == "t" else None

    def reserve_row(self):
        key = uuid.uuid4()
        self.ids.append(key)
        self.report["test_row_ids"] = [str(item) for item in self.ids]
        self.save()
        return key

    def insert_sql(self, key, label):
        return f"INSERT INTO {TABLE} (id, message) VALUES ('{key}', '{label}')"

    def preflight(self):
        for service in SERVICES:
            containers = self.compose("ps", "--all", "--quiet", service).splitlines()
            if not containers:
                raise RuntimeError(
                    f"No existing Compose container for {service}. From the repository root run: "
                    "docker compose -f lab1/compose.yaml up -d --build --wait --wait-timeout 180"
                )
            if len(containers) != 1 or not re.fullmatch(r"[0-9a-f]{12,64}", containers[0]):
                raise RuntimeError(f"Expected one container ID for {service}; got {containers!r}")
            self.containers[service] = containers[0]

        stopped = [service for service in SERVICES if not self.running(service)]
        if stopped:
            print(f"Starting existing containers: {', '.join(stopped)}", flush=True)
            self.compose("start", *stopped, timeout=120)
            self.report["preflight_started_services"] = stopped
            self.save()
        leader, standby = wait_for("one leader and one caught-up synchronous standby", self.sync_pair)
        checked(self.envoy(f"SELECT id FROM {TABLE} LIMIT 0"))
        configuration = self.compose("exec", "-T", leader, "patronictl",
                                     "-c", "/etc/patroni.yml", "show-config", "lab1")
        if not re.search(r"(?m)^synchronous_mode_strict:\s*false\s*$", configuration):
            raise RuntimeError("Expected synchronous_mode_strict: false in active Patroni config")
        return leader, standby

    def failover(self, leader, candidate):
        print(f"Failover: stop {leader}, expect {candidate}", flush=True)
        control = self.reserve_row()
        marker = self.reserve_row()
        checked(self.envoy(self.insert_sql(control, "lab1-measure-before-failover")))
        checked(self.envoy(self.insert_sql(marker, "lab1-measure-write-probe")))
        wait_for("both failover marker rows on the replica", lambda: self.ready(
            candidate, f"SELECT count(*) FROM {TABLE} WHERE id IN ('{control}', '{marker}')"
        ) == "2")

        read_sql = (f"SELECT host(inet_server_addr()) FROM {TABLE} WHERE id = '{control}' "
                    "AND NOT pg_is_in_recovery()")
        write_sql = (f"UPDATE {TABLE} SET message = md5(random()::text) WHERE id = '{marker}' "
                     "AND NOT pg_is_in_recovery() RETURNING host(inet_server_addr())")
        old_address = checked(self.envoy(read_sql))
        if not old_address:
            raise RuntimeError("Envoy baseline did not return the current leader address")

        origin = [None]
        end = threading.Event()
        samples = {"read": [], "write": [], "promotion": []}

        def lane(kind, query):
            while not end.is_set():
                began = time.perf_counter()
                try:
                    output = (self.envoy(query, PROBE_TIMEOUT) if kind != "promotion"
                              else self.direct(candidate, query, PROBE_TIMEOUT))
                    result = output.stdout.strip() if output.returncode == 0 else None
                    error = output.stderr.strip() if output.returncode else None
                except subprocess.TimeoutExpired:
                    result, error = None, "probe timeout"
                finished = time.perf_counter()
                samples[kind].append((began, finished, result or None, error))
                if origin[0] is not None and began >= origin[0]:
                    if kind == "promotion" and result == "f":
                        return
                    if kind != "promotion" and result and result != old_address:
                        return
                end.wait(max(0, self.interval - (finished - began)))

        jobs = [threading.Thread(target=lane, args=(kind, query), daemon=True)
                for kind, query in (("read", read_sql), ("write", write_sql),
                                    ("promotion", "SELECT pg_is_in_recovery()"))]
        record = {"old_leader": leader, "candidate": candidate, "old_address": old_address}
        self.report["failover"] = record
        started = []
        try:
            for job in jobs:
                job.start()
                started.append(job)
            wait_for("successful pre-stop read/write probes", lambda: all(
                any(sample[2] == old_address for sample in samples[kind])
                for kind in ("read", "write")), timeout=15, interval=self.interval)
            self.restart = leader
            record["stop_requested_utc"] = utc_now()
            origin[0] = time.perf_counter()
            self.compose("stop", leader)
            record["stop_command_returned_s"] = time.perf_counter() - origin[0]
            wait_for("successful post-stop read, write and promotion probes",
                     lambda: not any(job.is_alive() for job in jobs),
                     interval=self.interval)
        finally:
            end.set()
            for job in started:
                job.join()
            if origin[0] is not None:
                record["probes"] = {
                    kind: [
                        {"start_s": a - origin[0], "end_s": b - origin[0],
                         "result": value, "error": error}
                        for a, b, value, error in entries
                    ] for kind, entries in samples.items()
                }
            self.save()

        for kind in ("read", "write"):
            record[kind] = window(record["probes"][kind], old_address)
        if record["read"]["new_address"] != record["write"]["new_address"]:
            raise RuntimeError("Read and write probes reached different new addresses")
        record["promotion_first_observed_s"] = next(
            probe["end_s"] for probe in record["probes"]["promotion"]
            if probe["start_s"] >= 0 and probe["result"] == "f"
        )

        mark = time.perf_counter()
        self.compose("start", leader)
        pair = wait_for("former leader to return as synchronous standby", self.sync_pair)
        if pair != (candidate, leader):
            raise RuntimeError(f"Unexpected roles after restart: {pair}")
        self.restart = None
        record["synchronous_pair_observed_after_start_s"] = time.perf_counter() - mark
        self.save()
        print(f"  first read: {record['read']['first_new_success_from_stop_request_s']:.3f} s; "
              f"first write: {record['write']['first_new_success_from_stop_request_s']:.3f} s",
              flush=True)

    def cleanup(self):
        errors = []
        if self.restart:
            try:
                if not self.running(self.restart):
                    self.compose("start", self.restart)
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                errors.append(f"Container recovery: {exc}")

        if self.ids:
            try:
                leader, standby = wait_for("a synchronous pair for cleanup", self.sync_pair)
                filters = ", ".join(f"'{key}'" for key in self.ids)
                deleted = checked(self.direct(
                    leader, f"WITH deleted AS (DELETE FROM {TABLE} WHERE id IN ({filters}) "
                            "RETURNING id) SELECT count(*) FROM deleted"
                ))
                verify = f"SELECT count(*) FROM {TABLE} WHERE id IN ({filters})"
                if self.ready(leader, verify) != "0":
                    raise RuntimeError("Rows remain on the leader")
                wait_for("test row deletion on the replica",
                         lambda: self.ready(standby, verify) == "0")
                self.report["cleanup"] = {"verified": True, "deleted_rows": int(deleted),
                                          "leader": leader, "replica": standby}
            except (RuntimeError, subprocess.TimeoutExpired) as exc:
                errors.append(f"Test row cleanup: {exc}")
        if errors:
            self.report["cleanup_errors"] = errors
        return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose", type=Path, default=Path("lab1/compose.yaml"))
    parser.add_argument("--interval-ms", type=int, default=100, help="Target inter-probe interval")
    args = parser.parse_args()
    if not args.compose.is_file():
        parser.error("Run from the repository root or specify --compose LAB1_COMPOSE_PATH")
    if not 50 <= args.interval_ms <= 1000:
        parser.error("Use a 50-1000 ms probe interval")
    for command in ("docker", "psql"):
        if not shutil.which(command):
            parser.error(f"{command} was not found in PATH")
    try:
        checked(call(["docker", "compose", "-f", str(args.compose.resolve()), "ps", "-a"]))
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        parser.exit(1, f"Docker preflight failed: {exc}\n")

    password = getpass.getpass("PostgreSQL password for Envoy (Enter to use pgpass): ")
    suffix = f"{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}"
    output = Path.cwd() / f"lab1-measure-{suffix}.json"
    lab = Lab(args.compose.resolve(), password, args.interval_ms / 1000, output)
    failure = None
    try:
        lab.save()
        leader, standby = lab.preflight()
        lab.failover(leader, standby)
    except (Exception, KeyboardInterrupt) as exc:
        failure = str(exc) or type(exc).__name__
        lab.report["error"] = failure
    finally:
        errors = lab.cleanup()
        lab.report["finished_utc"] = utc_now()
        lab.save()

    print(f"Results: {output}", flush=True)
    if failure:
        print(f"Run failed: {failure}", flush=True)
    for error in errors:
        print(error, flush=True)
    if lab.report.get("cleanup", {}).get("verified"):
        print("Test row deletion verified on both nodes.", flush=True)
    return int(bool(failure or errors))


if __name__ == "__main__":
    raise SystemExit(main())
