-- Totals across every loaded cell; should match the local SQLite build.
SELECT
  (SELECT count(DISTINCT battery_id) FROM cycles)  AS cells,
  (SELECT count(*) FROM cycles)                    AS cycles,
  (SELECT count(*) FROM samples)                   AS samples,
  (SELECT count(*) FROM discharge_metrics)         AS discharges,
  (SELECT count(*) FROM quarantine)                AS quarantined;
