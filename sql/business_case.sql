-- The business case's gap and opportunity analysis in SQL (DuckDB), from the same inputs as
-- scripts/gap_analysis.py and scripts/opportunity.py. scripts/business_case_sql.py runs it and checks that
-- both give the same answer. ${DERIVED} is replaced with the data/derived folder.

-- Each network's mean 4G error on held-out drive-test areas (dB), for the bias-corrected scenario.
CREATE OR REPLACE VIEW bias AS
SELECT CASE operator WHEN 'Three' THEN '3uk' WHEN 'EE' THEN 'ee' WHEN 'O2' THEN 'o2' ELSE 'vf' END AS network,
       bias_db
FROM read_csv('${DERIVED}/coverage_validation.csv')
WHERE network = '4G' AND prediction = 'model + correction';

-- Which networks make up each operator, and whom each operator competes with. VodafoneThree is the better
-- of the Vodafone and Three signals; Vodafone alone is kept to show what the merger adds.
CREATE OR REPLACE TABLE operator_networks AS
SELECT * FROM (VALUES ('EE', 'ee'), ('VMO2', 'o2'), ('VodafoneThree', 'vf'), ('VodafoneThree', '3uk'),
                      ('Vodafone', 'vf')) t(operator, network);

CREATE OR REPLACE TABLE rivals AS
SELECT * FROM (VALUES ('EE', 'VMO2'), ('EE', 'VodafoneThree'), ('VMO2', 'EE'), ('VMO2', 'VodafoneThree'),
                      ('VodafoneThree', 'EE'), ('VodafoneThree', 'VMO2'),
                      ('Vodafone', 'EE'), ('Vodafone', 'VMO2')) t(operator, rival);

CREATE OR REPLACE VIEW addresses AS
SELECT uprn, population, lsoa21cd, msoa21cd, msoa_name, rsrp_4g_3uk, rsrp_4g_ee, rsrp_4g_o2, rsrp_4g_vf
FROM read_parquet('${DERIVED}/coverage_addresses.parquet')
WHERE population > 0;

-- Indoor 4G (Ofcom: -95 dBm or better) per address, operator and scenario.
CREATE OR REPLACE TABLE coverage AS
WITH signal AS (
    UNPIVOT addresses ON rsrp_4g_3uk, rsrp_4g_ee, rsrp_4g_o2, rsrp_4g_vf INTO NAME col VALUE rsrp
), scenario_signal AS (
    SELECT s.scenario, a.uprn, replace(a.col, 'rsrp_4g_', '') AS network,
           a.rsrp - CASE WHEN s.scenario = 'bias-corrected' THEN b.bias_db ELSE 0 END AS rsrp
    FROM signal a
    JOIN bias b ON b.network = replace(a.col, 'rsrp_4g_', '')
    CROSS JOIN (VALUES ('modelled'), ('bias-corrected')) s(scenario)
)
SELECT s.scenario, s.uprn, o.operator, max(s.rsrp) >= -95 AS indoor
FROM scenario_signal s JOIN operator_networks o USING (network)
GROUP BY ALL;

-- Per address and operator: covered, and covered by at least one rival.
CREATE OR REPLACE TABLE contest AS
SELECT c.scenario, c.uprn, c.operator, c.indoor,
       bool_or(r_cov.indoor) AS rival_indoor
FROM coverage c
JOIN rivals r ON r.operator = c.operator
JOIN coverage r_cov ON r_cov.scenario = c.scenario AND r_cov.uprn = c.uprn AND r_cov.operator = r.rival
GROUP BY ALL;

-- Competitive gaps per neighbourhood (MSOA).
CREATE OR REPLACE TABLE gap_msoa AS
WITH per_msoa AS (
    SELECT k.scenario, a.msoa21cd, a.msoa_name, k.operator,
           sum(a.population) AS residents,
           sum(a.population) FILTER (WHERE k.indoor) AS indoor,
           sum(a.population) FILTER (WHERE NOT k.indoor AND k.rival_indoor) AS at_risk,
           sum(a.population) FILTER (WHERE k.indoor AND NOT k.rival_indoor) AS advantage
    FROM contest k JOIN addresses a USING (uprn)
    GROUP BY ALL
), pct AS (
    SELECT *, 100 * coalesce(indoor, 0) / residents AS indoor_pct FROM per_msoa
), not_spots AS (
    SELECT c.scenario, a.msoa21cd, sum(a.population) AS not_spot
    FROM (SELECT scenario, uprn FROM coverage WHERE operator <> 'Vodafone'
          GROUP BY ALL HAVING NOT bool_or(indoor)) c
    JOIN addresses a USING (uprn)
    GROUP BY ALL
)
SELECT p.scenario, p.msoa21cd, p.msoa_name, p.operator, p.residents,
       coalesce(p.indoor, 0) AS indoor, coalesce(p.at_risk, 0) AS at_risk,
       coalesce(p.advantage, 0) AS advantage,
       round(p.indoor_pct, 1) AS indoor_pct,
       round(max(q.indoor_pct), 1) AS best_rival_pct,
       round(p.indoor_pct - max(q.indoor_pct), 1) AS gap_pp,
       coalesce(n.not_spot, 0) AS not_spot
FROM pct p
JOIN rivals r ON r.operator = p.operator
JOIN pct q ON q.scenario = p.scenario AND q.msoa21cd = p.msoa21cd AND q.operator = r.rival
LEFT JOIN not_spots n ON n.scenario = p.scenario AND n.msoa21cd = p.msoa21cd
GROUP BY p.scenario, p.msoa21cd, p.msoa_name, p.operator, p.residents, p.indoor, p.at_risk, p.advantage,
         p.indoor_pct, n.not_spot;

CREATE OR REPLACE TABLE gap_summary AS
SELECT scenario, operator, sum(residents) AS residents, sum(indoor) AS indoor, sum(at_risk) AS at_risk,
       sum(advantage) AS advantage, round(100 * sum(indoor) / sum(residents), 1) AS indoor_pct,
       (SELECT sum(not_spot) FROM (SELECT DISTINCT scenario, msoa21cd, not_spot FROM gap_msoa) d
        WHERE d.scenario = g.scenario) AS not_spot
FROM gap_msoa g
GROUP BY ALL;

-- Opportunity: the customers behind each gap, from the Census 2021 profile of each address's LSOA.
-- customers = adult residents; value = employed or student share, relative to the area average.
CREATE OR REPLACE TABLE opportunity_msoa AS
WITH demo AS (
    SELECT * FROM read_csv('${DERIVED}/lsoa_demographics.csv')
), area AS (
    SELECT sum((employed_share + student_share) * residents) / sum(adult_share * residents) AS active
    FROM demo
), people AS (
    SELECT a.uprn, a.msoa21cd, a.msoa_name, a.population,
           a.population * d.adult_share AS customers,
           (d.employed_share + d.student_share) / d.adult_share / area.active AS value,
           a.population * d.student_share AS students, a.population * d.employed_share AS employed
    FROM addresses a JOIN demo d USING (lsoa21cd) CROSS JOIN area
)
SELECT k.scenario, p.msoa21cd, p.msoa_name, k.operator,
       round(sum(p.customers) FILTER (WHERE NOT k.indoor AND k.rival_indoor), 1) AS defend_customers,
       round(sum(p.customers * p.value) FILTER (WHERE NOT k.indoor AND k.rival_indoor), 1) AS defend_score,
       round(sum(p.customers) FILTER (WHERE k.indoor AND NOT k.rival_indoor), 1) AS attack_customers,
       round(sum(p.customers * p.value) FILTER (WHERE k.indoor AND NOT k.rival_indoor), 1) AS attack_score,
       sum(p.population) AS residents,
       round(100 * sum(p.students) / sum(p.population), 1) AS student_pct,
       round(100 * sum(p.employed) / sum(p.population), 1) AS employed_pct
FROM contest k JOIN people p USING (uprn)
WHERE k.operator <> 'Vodafone'
GROUP BY ALL;
