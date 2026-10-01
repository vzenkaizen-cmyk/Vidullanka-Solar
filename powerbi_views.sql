-- ==========================================================
-- POWER BI SQL VIEWS
-- Run these in your PostgreSQL / Neon database.
-- Then connect Power BI Desktop directly to PostgreSQL.
-- ==========================================================

CREATE OR REPLACE VIEW vw_powerbi_maintenance AS
SELECT
    mr.id AS record_id,
    p.code AS plant_code,
    p.name AS plant_name,
    p.capacity_mw,
    mr.task_date,
    mr.category,
    mr.asset_id,
    mr.status,
    mr.worker_id,
    u.full_name AS worker_name,
    u.email AS worker_email,
    u.role AS worker_role,
    mr.remarks,
    mr.completed_at
FROM maintenance_records mr
JOIN plants p ON p.id = mr.plant_id
LEFT JOIN users u ON u.id = mr.worker_id;

CREATE OR REPLACE VIEW vw_powerbi_repairs AS
SELECT
    ri.id AS repair_id,
    p.code AS plant_code,
    p.name AS plant_name,
    ri.source_category AS category,
    ri.asset_id,
    ri.component,
    ri.issue,
    ri.reason_not_completed,
    ri.status,
    ri.loss_percent,
    ri.assigned_to,
    au.full_name AS assigned_to_name,
    ri.created_by,
    cu.full_name AS created_by_name,
    ri.created_at,
    ri.completed_at
FROM repair_items ri
JOIN plants p ON p.id = ri.plant_id
LEFT JOIN users au ON au.id = ri.assigned_to
LEFT JOIN users cu ON cu.id = ri.created_by;

CREATE OR REPLACE VIEW vw_powerbi_notifications AS
SELECT
    n.id,
    p.code AS plant_code,
    p.name AS plant_name,
    u.full_name AS recipient,
    u.email AS recipient_email,
    n.notification_type,
    n.title,
    n.category,
    n.asset_id,
    n.severity,
    n.is_read,
    n.email_sent,
    n.created_at
FROM notifications n
LEFT JOIN plants p ON p.id = n.plant_id
LEFT JOIN users u ON u.id = n.user_id;

CREATE OR REPLACE VIEW vw_powerbi_reminders AS
SELECT
    r.id,
    p.code AS plant_code,
    p.name AS plant_name,
    r.category,
    r.frequency,
    r.due_date,
    r.status,
    r.created_at,
    r.sent_at
FROM maintenance_reminders r
JOIN plants p ON p.id = r.plant_id;


-- ==========================================================
-- POWER BI DASHBOARD SUMMARY
-- ==========================================================
CREATE OR REPLACE VIEW vw_powerbi_dashboard_summary AS
SELECT
    p.id AS plant_id,
    p.code AS plant_code,
    p.name AS plant_name,
    p.capacity_mw,
    p.inverter_count,
    p.table_count,
    p.zone_count,
    mr.task_date,
    mr.category,
    mr.asset_id,
    mr.status,
    mr.worker_id,
    u.full_name AS worker_name,
    CASE
        WHEN mr.status = 'Completed' THEN 1
        ELSE 0
    END AS completed_flag,
    CASE
        WHEN mr.status = 'Failed' THEN 1
        ELSE 0
    END AS red_flag
FROM plants p
LEFT JOIN maintenance_records mr ON mr.plant_id = p.id
LEFT JOIN users u ON u.id = mr.worker_id;
