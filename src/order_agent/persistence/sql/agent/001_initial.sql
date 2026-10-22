CREATE SCHEMA IF NOT EXISTS agent;
CREATE TABLE IF NOT EXISTS agent.tasks (
 id text PRIMARY KEY, order_id text NOT NULL, status text NOT NULL, planner_mode text NOT NULL,
 fault text NOT NULL DEFAULT 'none', last_error text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS agent.events (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, task_id text NOT NULL REFERENCES agent.tasks(id),
 type text NOT NULL, data jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS events_task ON agent.events(task_id,id);
CREATE TABLE IF NOT EXISTS agent.approvals (
 task_id text NOT NULL REFERENCES agent.tasks(id), proposal_version integer NOT NULL,
 request_id text NOT NULL, payload_hash text NOT NULL, decision jsonb NOT NULL,
 decided_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(task_id,proposal_version), UNIQUE(task_id,request_id)
);
CREATE TABLE IF NOT EXISTS agent.fault_hits (
 task_id text NOT NULL, point text NOT NULL, PRIMARY KEY(task_id,point)
);
