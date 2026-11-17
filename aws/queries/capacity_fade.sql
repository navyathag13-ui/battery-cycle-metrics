-- Per cell: first and last capacity and how much was lost.
SELECT
  battery_id,
  count(*)                                   AS discharges,
  round(min_by(capacity_ah, discharge_no), 3) AS first_ah,
  round(max_by(capacity_ah, discharge_no), 3) AS last_ah,
  round(100 * (1 - max_by(capacity_ah, discharge_no) / min_by(capacity_ah, discharge_no)), 1) AS pct_lost
FROM discharge_metrics
GROUP BY battery_id
ORDER BY battery_id;
