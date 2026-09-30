#!/usr/bin/env python3
"""Compare the REAL prod MariaDB schema with a freshly built one.

Usage (from the repo root):
    python3 scripts/schema_drift_report.py [--prod-container mariadb-hygie]

What it does
  1. Starts ONE throwaway `mariadb:11` container (`hygie-schema-scratch`,
     512 MB, default bridge network, random root password never printed nor
     written anywhere) and applies THIS checkout's `init_db()` +
     `run_migrations()` on it.
  2. Reads the schema of the prod database through `docker exec -i
     <prod-container> sh -c ...` with information_schema SELECTs ONLY. The
     credentials are read from the prod container's own environment inside
     that `sh -c` (least-privilege app user) and never leave it.
  3. Prints a readable diff (columns type/nullable/default/extra/charset,
     indexes, foreign keys, engine/collation, column order).
  4. ALWAYS stops and removes the scratch container (finally block) and
     re-checks with `docker ps -a` that it is gone.

Read-only on prod: the only SQL sent there is the constant SELECT batch in
_INTROSPECTION_SQL. Exit code: 0 no drift, 1 drift found, 2 tooling error.
"""
from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
import time
from collections import defaultdict

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRATCH = "hygie-schema-scratch"
IMAGE = "mariadb:11"
SCRATCH_DB = "hygie"

# Tagged rows, one constant batch of SELECTs (information_schema only).
# Scoped by DATABASE(), i.e. the -D given on the mariadb command line.
_INTROSPECTION_SQL = """
SELECT 'VERSION', VERSION();
SELECT 'SCHEMA', default_character_set_name, default_collation_name
  FROM information_schema.SCHEMATA WHERE schema_name = DATABASE();
SELECT 'TABLE', table_name, engine, table_collation, IFNULL(row_format,'')
  FROM information_schema.TABLES
  WHERE table_schema = DATABASE() AND table_type = 'BASE TABLE' ORDER BY table_name;
SELECT 'COL', table_name, column_name, ordinal_position, column_type, is_nullable,
       IFNULL(column_default,'<NULL>'), IFNULL(extra,''),
       IFNULL(character_set_name,''), IFNULL(collation_name,'')
  FROM information_schema.COLUMNS WHERE table_schema = DATABASE()
  ORDER BY table_name, ordinal_position;
SELECT 'IDX', table_name, index_name, non_unique, seq_in_index, column_name, index_type
  FROM information_schema.STATISTICS WHERE table_schema = DATABASE()
  ORDER BY table_name, index_name, seq_in_index;
SELECT 'FK', k.table_name, k.constraint_name, k.column_name, k.referenced_table_name,
       k.referenced_column_name, r.delete_rule, r.update_rule
  FROM information_schema.KEY_COLUMN_USAGE k
  JOIN information_schema.REFERENTIAL_CONSTRAINTS r
    ON r.constraint_schema = k.constraint_schema AND r.constraint_name = k.constraint_name
       AND r.table_name = k.table_name
  WHERE k.table_schema = DATABASE() AND k.referenced_table_name IS NOT NULL
  ORDER BY k.table_name, k.constraint_name, k.ordinal_position;
"""


# ─── parsing / diff (pure, unit-tested) ──────────────────────────────────────
def parse_introspection(text: str) -> dict:
    """Tab-separated tagged rows -> normalized schema dict."""
    s = {"version": "", "schema": (), "tables": {}, "columns": defaultdict(dict),
         "order": defaultdict(list), "indexes": defaultdict(lambda: defaultdict(list)),
         "unique": {}, "fks": defaultdict(lambda: defaultdict(list))}
    for line in text.splitlines():
        f = line.split("\t")
        tag = f[0]
        if tag == "VERSION":
            s["version"] = f[1]
        elif tag == "SCHEMA":
            s["schema"] = (f[1], f[2])
        elif tag == "TABLE":
            s["tables"][f[1]] = {"engine": f[2], "collation": f[3], "row_format": f[4]}
        elif tag == "COL":
            _, t, c, pos, ctype, nullable, dflt, extra, cs, coll = f
            s["columns"][t][c] = {"type": ctype, "nullable": nullable, "default": dflt,
                                  "extra": extra, "charset": cs, "collation": coll}
            s["order"][t].append((int(pos), c))
        elif tag == "IDX":
            _, t, name, non_unique, _seq, col, itype = f
            s["indexes"][t][name].append(col)
            s["unique"][(t, name)] = (non_unique == "0", itype)
        elif tag == "FK":
            _, t, name, col, rt, rc, dele, upd = f
            s["fks"][t][name].append((col, rt, rc, dele, upd))
    return s


def diff_schemas(prod: dict, fresh: dict) -> list[str]:
    """Human-readable diff lines; empty list = identical (on what is compared)."""
    out: list[str] = []
    if prod["schema"] != fresh["schema"]:
        out.append(f"[schema] default charset/collation: prod={prod['schema']} fresh={fresh['schema']}")
    for t in sorted(set(prod["tables"]) - set(fresh["tables"])):
        out.append(f"[table] only in PROD: {t}")
    for t in sorted(set(fresh["tables"]) - set(prod["tables"])):
        out.append(f"[table] only in FRESH: {t}")
    for t in sorted(set(prod["tables"]) & set(fresh["tables"])):
        for k in ("engine", "collation", "row_format"):
            if prod["tables"][t][k] != fresh["tables"][t][k]:
                out.append(f"[table] {t}.{k}: prod={prod['tables'][t][k]!r} fresh={fresh['tables'][t][k]!r}")
        out.extend(_diff_columns(t, prod, fresh))
        out.extend(_diff_indexes(t, prod, fresh))
        out.extend(_diff_fks(t, prod, fresh))
    return out


def _diff_columns(t: str, prod: dict, fresh: dict) -> list[str]:
    out = []
    pc, fc = prod["columns"][t], fresh["columns"][t]
    for c in sorted(set(pc) - set(fc)):
        out.append(f"[column] {t}.{c}: only in PROD ({pc[c]['type']})")
    for c in sorted(set(fc) - set(pc)):
        out.append(f"[column] {t}.{c}: only in FRESH ({fc[c]['type']})")
    for c in sorted(set(pc) & set(fc)):
        for k in ("type", "nullable", "default", "extra", "charset", "collation"):
            if pc[c][k] != fc[c][k]:
                out.append(f"[column] {t}.{c}.{k}: prod={pc[c][k]!r} fresh={fc[c][k]!r}")
    p_order = [c for _, c in sorted(prod["order"][t]) if c in fc]
    f_order = [c for _, c in sorted(fresh["order"][t]) if c in pc]
    if p_order != f_order:
        out.append(f"[order] {t}: column order differs (cosmetic) prod={p_order} fresh={f_order}")
    return out


def _diff_indexes(t: str, prod: dict, fresh: dict) -> list[str]:
    out = []
    pi, fi = prod["indexes"][t], fresh["indexes"][t]
    for n in sorted(set(pi) - set(fi)):
        out.append(f"[index] {t}.{n}({','.join(pi[n])}): only in PROD")
    for n in sorted(set(fi) - set(pi)):
        out.append(f"[index] {t}.{n}({','.join(fi[n])}): only in FRESH")
    for n in sorted(set(pi) & set(fi)):
        if pi[n] != fi[n]:
            out.append(f"[index] {t}.{n} columns: prod={pi[n]} fresh={fi[n]}")
        if prod["unique"][(t, n)] != fresh["unique"][(t, n)]:
            out.append(f"[index] {t}.{n} (unique,type): prod={prod['unique'][(t, n)]} "
                       f"fresh={fresh['unique'][(t, n)]}")
    return out


def _diff_fks(t: str, prod: dict, fresh: dict) -> list[str]:
    out = []
    pf, ff = prod["fks"][t], fresh["fks"][t]
    for n in sorted(set(pf) - set(ff)):
        out.append(f"[fk] {t}.{n}: only in PROD {pf[n]}")
    for n in sorted(set(ff) - set(pf)):
        out.append(f"[fk] {t}.{n}: only in FRESH {ff[n]}")
    for n in sorted(set(pf) & set(ff)):
        if pf[n] != ff[n]:
            out.append(f"[fk] {t}.{n}: prod={pf[n]} fresh={ff[n]}")
    return out


# ─── docker plumbing ─────────────────────────────────────────────────────────
def _run(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def _scratch_exists() -> bool:
    out = _run(["docker", "ps", "-a", "--filter", f"name=^{SCRATCH}$", "--format", "{{.Names}}"])
    return SCRATCH in out.stdout.split()


def _scrub(text: str, secret: str) -> str:
    return text.replace(secret, "***") if secret else text


def _introspect_via_exec(container: str, user_expr: str, pw_env: str, db_env: str) -> dict:
    """Run the constant SELECT batch inside `container`; creds come from ITS env."""
    shell = (f'MYSQL_PWD="${pw_env}" exec mariadb -u{user_expr} -D "${db_env}" '
             f"--batch --skip-column-names")
    res = _run(["docker", "exec", "-i", container, "sh", "-c", shell], input=_INTROSPECTION_SQL)
    if res.returncode != 0:
        raise RuntimeError(f"introspection on {container} failed: {res.stderr.strip()[:500]}")
    if "COL\t" not in res.stdout:
        raise RuntimeError(f"introspection on {container} returned no column rows "
                           "(wrong database? empty result must not pass as 'no drift')")
    return parse_introspection(res.stdout)


def _wait_ready(root_pw_env: str, timeout: int = 120) -> None:
    """Ready = the *final* server answers over the network (the entrypoint runs a
    temporary socket-only server first, so a single ping is not enough)."""
    deadline, ok = time.time() + timeout, 0
    while time.time() < deadline:
        res = _run(["docker", "exec", "-i", SCRATCH, "sh", "-c",
                    f'MYSQL_PWD="${root_pw_env}" exec mariadb -uroot -h127.0.0.1 -N -e "SELECT 1"'])
        ok = ok + 1 if res.returncode == 0 and res.stdout.strip() == "1" else 0
        if ok >= 2:
            return
        time.sleep(2)
    raise RuntimeError("scratch MariaDB did not become ready in time")


def _apply_fresh_schema(ip: str, password: str) -> str:
    code = (
        "import asyncio\n"
        "from backend.db import engine\n"
        "from backend.db.schema import init_db\n"
        "from backend.db.migrations import run_migrations, pending_migration_ids\n"
        "async def main():\n"
        "    assert engine.DIALECT == 'mariadb'\n"
        "    await engine.init_db_pool()\n"
        "    try:\n"
        "        await init_db()\n"
        "        n = await run_migrations()\n"
        "        print('migrations applied:', n, 'pending after:', await pending_migration_ids())\n"
        "    finally:\n"
        "        await engine.close_db_pool()\n"
        "asyncio.run(main())\n"
    )
    env = dict(os.environ, PYTHONPATH=REPO_ROOT,
               DATABASE_URL=f"mysql+aiomysql://root:{password}@{ip}:3306/{SCRATCH_DB}",
               HYGIE_ENCRYPTION_KEY=os.environ.get("HYGIE_ENCRYPTION_KEY",
                                                   "dGVzdGtleXRlc3RrZXl0ZXN0a2V5dGVzdGtleXRlc3Q="))
    res = _run([sys.executable, "-c", code], env=env, cwd=REPO_ROOT)
    if res.returncode != 0:
        raise RuntimeError("init_db/run_migrations on scratch failed:\n"
                           + _scrub(res.stderr[-1500:], password))
    return res.stdout.strip()


def _start_scratch(password: str) -> str:
    if _scratch_exists():
        raise RuntimeError(f"container {SCRATCH} already exists — refusing to touch it")
    env = dict(os.environ, MARIADB_ROOT_PASSWORD=password)  # by name in argv: no secret in argv
    res = _run(["docker", "run", "-d", "--name", SCRATCH, "--memory", "512m",
                "-e", "MARIADB_ROOT_PASSWORD", "-e", f"MARIADB_DATABASE={SCRATCH_DB}", IMAGE], env=env)
    if res.returncode != 0:
        raise RuntimeError(f"docker run failed: {res.stderr.strip()[:300]}")
    ip = _run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}",
               SCRATCH]).stdout.strip()
    if not ip:
        raise RuntimeError("could not determine scratch container IP")
    return ip


def _cleanup() -> None:
    if _scratch_exists():
        _run(["docker", "stop", SCRATCH])
        _run(["docker", "rm", SCRATCH])
    left = _scratch_exists()
    print(f"[cleanup] {SCRATCH} {'STILL PRESENT' if left else 'removed (docker ps -a: absent)'}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--prod-container", default="mariadb-hygie")
    args = ap.parse_args()

    password = secrets.token_urlsafe(24)
    try:
        ip = _start_scratch(password)
        print(f"[scratch] {SCRATCH} started ({IMAGE}, 512m, ip {ip})")
        _wait_ready("MARIADB_ROOT_PASSWORD")
        print("[scratch]", _apply_fresh_schema(ip, password))
        fresh = _introspect_via_exec(SCRATCH, "root", "MARIADB_ROOT_PASSWORD", "MARIADB_DATABASE")
    except Exception as e:  # noqa: BLE001 - tooling boundary, message is scrubbed
        print(f"ERROR: {_scrub(str(e), password)}", file=sys.stderr)
        _cleanup()
        return 2
    try:
        prod = _introspect_via_exec(args.prod_container, '"$MYSQL_USER"', "MYSQL_PASSWORD", "MYSQL_DATABASE")
    except Exception as e:  # noqa: BLE001
        print(f"ERROR: {e}", file=sys.stderr)
        return 2
    finally:
        _cleanup()

    print(f"\n=== Schema drift report: PROD ({args.prod_container}, MariaDB {prod['version']}) "
          f"vs FRESH (init_db+migrations, MariaDB {fresh['version']}) ===")
    print(f"tables: prod={len(prod['tables'])} fresh={len(fresh['tables'])}; "
          f"columns: prod={sum(map(len, prod['columns'].values()))} "
          f"fresh={sum(map(len, fresh['columns'].values()))}")
    lines = diff_schemas(prod, fresh)
    if not lines:
        print("NO DRIFT on: tables, engine, collation, columns (type/null/default/extra/charset), "
              "column order, indexes, foreign keys")
        return 0
    print(f"{len(lines)} difference(s):")
    for line in lines:
        print(" ", line)
    return 1


if __name__ == "__main__":
    sys.exit(main())
