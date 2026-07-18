-- The public/private boundary is the data layer (not the UI, not the API).
-- Mechanism 1 — public_reader: column-scoped SELECT. Withheld columns are
-- not filtered; they are ungranted. A new column is private until listed
-- here. Grant lists MUST mirror build_demo.py PUBLIC / EVENT_PUBLIC exactly
-- while both deploy paths exist (probes/grant_parity.py enforces this).
CREATE ROLE public_reader NOLOGIN;
GRANT USAGE ON SCHEMA public TO public_reader;
GRANT SELECT (id, source, external_id, url, title, company, category,
              job_type, location, salary, description, publication_date,
              fetched_at, content_hash, prefilter_pass, ladder_match,
              relevance_score, score_reason, status, status_updated_at)
    ON jobs TO public_reader;
GRANT SELECT (id, job_id, from_status, to_status, at)
    ON job_events TO public_reader;
-- Withheld: jobs.notes, jobs.cover_letter, job_events.note

-- Mechanism 2 — orchestrator_app: append-only trail enforced by permissions,
-- not discipline. No UPDATE, no DELETE on job_events, ever.
CREATE ROLE orchestrator_app LOGIN;
GRANT SELECT, INSERT, UPDATE ON jobs TO orchestrator_app;
GRANT SELECT, INSERT ON job_events TO orchestrator_app;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO orchestrator_app;
