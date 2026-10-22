-- P2 fixtures are versioned JSON snapshots, not yet a complete inventory system.
ALTER TABLE business.orders ADD COLUMN planning_context jsonb;
