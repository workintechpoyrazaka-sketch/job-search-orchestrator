"""Deploy gate: assert the operator apply route's gate behaves. Exit 1 on any
failure.

Exercises POST /api/operator/jobs/{id}/apply end to end against a THROWAWAY
SQLite DB — never the live jobs.db. The database is swapped by monkeypatching
get_connection; the sqlite_writer_conn DEPENDENCY is left untouched, so the
auth this probe asserts is the REAL Depends(require_operator) that ships. That
is deliberate: the write path is the dependency re-wired at cutover, and if a
future Postgres writer dep forgets require_operator, the no-token / wrong-token
checks below turn red. A gate that has only ever passed is untested; this one
asserts the route both REJECTS (401/400/409) and APPLIES (200), and that the
resulting SQLite trail honors verify_invariants.

Single authority unchanged: the transition still runs through apply_to_job ->
_apply_transition. This probe proves the HTTP surface funnels into it correctly;
it does not reimplement it.
"""
import os
import sys
import tempfile
from pathlib import Path

# Dummy env so module-load reads succeed with no Postgres and a known token.
# setdefault: a configured shell keeps its real values; a fresh shell still runs
# (the probe is intentionally infra-free — no live DB, no real secret needed).
os.environ.setdefault("OPERATOR_TOKEN", "probe-token")
os.environ.setdefault("DATABASE_URL", "postgresql://unused/none")

from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.api.db as dbmod
from src.core.storage import get_connection as _real_get_connection
from src.api.routes_operator import router
from src.core.tracking import verify_invariants

TOKEN = os.environ["OPERATOR_TOKEN"]
SCRATCH = Path(tempfile.mktemp(suffix=".db"))


def _seed():
    c = _real_get_connection(SCRATCH)
    c.executescript(
        """
        CREATE TABLE jobs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, status TEXT DEFAULT 'new',
          status_updated_at TEXT, title TEXT, company TEXT, location TEXT,
          url TEXT, description TEXT);
        CREATE TABLE job_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT, job_id INTEGER NOT NULL,
          from_status TEXT, to_status TEXT NOT NULL, at TEXT NOT NULL, note TEXT,
          FOREIGN KEY (job_id) REFERENCES jobs(id));
        """
    )
    c.execute("INSERT INTO jobs (id,status,title,company,location,url,description) "
              "VALUES (1,'new','Data Analyst','Acme','Remote','http://x','Fully remote, work anywhere.')")
    c.execute("INSERT INTO jobs (id,status,title,company,location,url,description) "
              "VALUES (2,'new','BI Dev','Globex','US','http://y','Must be located in the US. No sponsorship.')")
    c.commit()
    c.close()


def main():
    _seed()
    # Swap the DB under the REAL dependency: sqlite_writer_conn still runs its
    # Depends(require_operator); only the connection it opens points at scratch.
    dbmod.get_connection = lambda: _real_get_connection(SCRATCH)

    app = FastAPI()
    app.include_router(router)
    cl = TestClient(app)
    auth = {"Authorization": f"Bearer {TOKEN}"}
    bad = {"Authorization": "Bearer wrong"}
    U = "/api/operator/jobs/{}/apply"

    violations = []

    def check(label, r, want_status, want_pred=None):
        if r.status_code != want_status:
            violations.append(f"{label}: status {r.status_code} != {want_status} ({r.json()})")
            return
        if want_pred:
            try:
                ok = want_pred(r.json())
            except Exception:
                ok = False
            if not ok:
                violations.append(f"{label}: body predicate failed ({r.json()})")

    # --- auth: the checks that turn red if a cutover drops require_operator ---
    # No confirm: a dropped-auth request must not WRITE while we're proving the
    # gate — it stops at the confirm gate (409), keeping job1 pristine.
    check("no token", cl.post(U.format(1), json={"note": "n"}), 401)
    check("wrong token", cl.post(U.format(1), json={"note": "n"}, headers=bad), 401)

    # --- not found ---
    check("missing job", cl.post(U.format(999), json={"note": "n", "confirm": True}, headers=auth), 404)

    # --- clean job 1: gate then apply ---
    check("no confirm -> surface", cl.post(U.format(1), json={"note": "N"}, headers=auth), 409,
          lambda b: b["detail"]["error"] == "confirmation required" and b["detail"]["red_flags"] == [])
    check("empty note", cl.post(U.format(1), json={"note": "  ", "confirm": True}, headers=auth), 400)
    check("apply", cl.post(U.format(1), json={"note": "Greenhouse 07-22", "confirm": True}, headers=auth), 200,
          lambda b: b["status"] == "applied")
    check("re-apply illegal", cl.post(U.format(1), json={"note": "again", "confirm": True}, headers=auth), 409)

    # --- red-flag job 2: escalation ---
    check("flags, no ack -> refuse", cl.post(U.format(2), json={"note": "x", "confirm": True}, headers=auth), 409,
          lambda b: b["detail"]["red_flags"])
    check("flags, ack -> apply", cl.post(
        U.format(2), json={"note": "x", "confirm": True, "acknowledge_red_flags": True}, headers=auth), 200,
        lambda b: b["status"] == "applied")

    # --- the writes are real and honor the invariants ---
    v = _real_get_connection(SCRATCH)
    inv = verify_invariants(v)
    v.close()
    if inv:
        violations.append(f"invariants violated after applies: {inv}")

    SCRATCH.unlink(missing_ok=True)

    if violations:
        print(f"apply gate FAILED ({len(violations)}):")
        for x in violations:
            print(f"  {x}")
        sys.exit(1)
    print("apply gate OK: auth 401s, confirm surface, red-flag escalation, "
          "real applied write, invariants clean")
    sys.exit(0)


if __name__ == "__main__":
    main()
