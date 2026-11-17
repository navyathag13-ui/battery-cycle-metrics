-- Cycles the checks set aside, by reason.
SELECT reasons, count(*) AS cycles
FROM quarantine
GROUP BY reasons
ORDER BY cycles DESC, reasons;
