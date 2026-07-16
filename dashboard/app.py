"""
Job-Search Orchestrator - read-only analytics cockpit.

Aggregate views over the pipeline database:
funnel, source breakdown, score distribution, drafted queue, audit trail.

Read-only by construction: the DB is opened with mode=ro, so this app
CANNOT mutate pipeline state. The apply path stays in the CLI
(python -m src.apply <id>), where the Module 4 forcing function lives.
There is no write path here, by design.

Data source is chosen by the DB_PATH env var:
  - unset -> data/jobs.db   (local cockpit, real data)
  - set   -> that path      (deploy points DB_PATH at data/demo.db)

Run from the repo root:  streamlit run dashboard/app.py
"""

import os
import sqlite3
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

DB_PATH = os.environ.get("DB_PATH", "data/jobs.db")

# Lifecycle order for the funnel; missing statuses render as 0.
STATUS_ORDER = ["new", "drafted", "applied", "archived"]

# Seniority band of interest, per the scoring layer (Path 1 gate).
BAND_LO, BAND_HI = 78, 83
SENIOR_RE = r"\b(?:senior|sr|lead|principal|staff)\b"


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open the database read-only. Fail loud if the file is missing."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found at '{db_path}'. "
            "Local dev expects data/jobs.db; a deploy must set DB_PATH "
            "to a committed database (e.g. data/demo.db). "
            "Also make sure you launched from the repo root."
        )
    # mode=ro: read-only connection. The app is structurally incapable
    # of writing. Writes belong to the CLI, not the dashboard.
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def q(conn: sqlite3.Connection, sql: str) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn)


# ---- page setup ----
st.set_page_config(page_title="Orchestrator Cockpit", layout="wide")

try:
    conn = get_connection(DB_PATH)
except FileNotFoundError as err:
    st.error(str(err))
    st.stop()

st.title("Job-Search Orchestrator - Cockpit")
st.caption(f"Data source: `{DB_PATH}` - read-only")

# ---- totals ----
total = int(q(conn, "SELECT COUNT(*) AS n FROM jobs")["n"].iloc[0])
events_total = int(q(conn, "SELECT COUNT(*) AS n FROM job_events")["n"].iloc[0])

col_a, col_b = st.columns(2)
col_a.metric("Total jobs", total)
col_b.metric("Recorded events", events_total)

# ---- funnel: status counts, in lifecycle order ----
# st.bar_chart sorts the nominal axis alphabetically, which scrambles a
# funnel (applied/archived/drafted/new). Altair with an explicit sort on
# the x encoding forces new -> drafted -> applied -> archived.
st.subheader("Pipeline funnel")
funnel_df = (
    q(conn, "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status")
    .set_index("status")
    .reindex(STATUS_ORDER, fill_value=0)
    .reset_index()
)
funnel_chart = (
    alt.Chart(funnel_df)
    .mark_bar()
    .encode(
        x=alt.X("status:N", sort=STATUS_ORDER, title="status"),
        y=alt.Y("n:Q", title="count"),
    )
)
st.altair_chart(funnel_chart, width="stretch")

# ---- source breakdown ----
st.subheader("Jobs by source")
by_source = (
    q(
        conn,
        "SELECT source, COUNT(*) AS n FROM jobs "
        "GROUP BY source ORDER BY n DESC",
    )
    .set_index("source")["n"]
)
st.bar_chart(by_source)

# ---- score distribution (NULL-aware) ----
st.subheader("Relevance score distribution")
scored = q(
    conn,
    "SELECT relevance_score, title FROM jobs "
    "WHERE relevance_score IS NOT NULL",
)
n_scored = len(scored)
n_unscored = total - n_scored
pct = f"{n_scored / total:.0%}" if total else "0%"
st.caption(
    f"{n_scored} scored - {n_unscored} unscored (NULL). "
    f"Chart covers the {pct} of jobs that have been scored; "
    f"unscored jobs are absent, not zero. "
    f"Band of interest: {BAND_LO}-{BAND_HI}. "
    f"Title marker is a flag to look, not a verdict -- seniority stated only "
    f"in the description is invisible here (row 400)."
)
if n_scored:
    def _bucket(s: int) -> str:
        if s < BAND_LO:
            return f"below {BAND_LO}"
        if s <= BAND_HI:
            return f"{BAND_LO}-{BAND_HI} band"
        return f"above {BAND_HI}"

    scored["bucket"] = scored["relevance_score"].apply(_bucket)
    scored["title_says"] = (
        scored["title"]
        .str.contains(SENIOR_RE, case=False, regex=True, na=False)
        .map({True: "senior marker in title", False: "no marker"})
    )
    chart = (
        alt.Chart(scored)
        .mark_bar()
        .encode(
            x=alt.X(
                "bucket:N",
                sort=[f"below {BAND_LO}", f"{BAND_LO}-{BAND_HI} band",
                      f"above {BAND_HI}"],
                title="relevance score",
            ),
            y=alt.Y("count()", title="jobs"),
            color=alt.Color("title_says:N", title=None),
            tooltip=["bucket", "title_says", "count()"],
        )
    )
    st.altair_chart(chart, use_container_width=True)
else:
    st.info("No scored jobs yet.")

# ---- drafted queue (safe money-view: no cover_letter, no notes) ----
st.subheader("Drafted queue")
drafted = q(
    conn,
    """
    SELECT id, title, company, source, relevance_score, url
    FROM jobs
    WHERE status = 'drafted'
    ORDER BY relevance_score DESC
    """,
)
if len(drafted):
    st.dataframe(drafted, width="stretch", hide_index=True)
else:
    st.info("No drafted jobs.")

# ---- audit trail (honest empty state) ----
st.subheader("Audit trail (job_events)")
if events_total:
    events = q(
        conn,
        """
        SELECT e.id, e.job_id, j.title,
               e.from_status, e.to_status, e.at, e.note
        FROM job_events e
        LEFT JOIN jobs j ON j.id = e.job_id
        ORDER BY e.at DESC, e.id DESC
        """,
    )
    st.dataframe(events, width="stretch", hide_index=True)
else:
    st.info(
        "No state transitions recorded yet. The trail populates as you "
        "apply via `python -m src.apply <id>`."
    )

conn.close()
