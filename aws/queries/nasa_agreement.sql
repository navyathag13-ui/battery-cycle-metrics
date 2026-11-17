-- How often my coulomb-counted capacity (to 2.7 V) lands within 0.01 Ah of NASA's own figure.
SELECT
  count(*)                                                        AS discharges,
  count_if(abs(capacity_to_2v7_ah - nasa_capacity_ah) < 0.01)     AS within_0_01_ah,
  round(100.0 * count_if(abs(capacity_to_2v7_ah - nasa_capacity_ah) < 0.01) / count(*), 1) AS pct
FROM discharge_metrics;
