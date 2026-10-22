ALTER TABLE agent.tasks ADD COLUMN decision_protocol text NOT NULL DEFAULT 'v1';
ALTER TABLE agent.tasks ADD COLUMN model_configuration jsonb;
CREATE TABLE agent.model_calls (
 id text PRIMARY KEY,
 task_id text NOT NULL REFERENCES agent.tasks(id),
 generation text NOT NULL, turn integer NOT NULL,
 provider text NOT NULL, model text NOT NULL, input_hash text NOT NULL,
 status text NOT NULL,
 reserved_input integer NOT NULL, reserved_output integer NOT NULL,
 actual_input integer, actual_output integer,
 response jsonb, error_type text, elapsed_ms integer,
 created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX model_calls_task ON agent.model_calls(task_id, generation, turn);
CREATE TABLE agent.investigations (
 task_id text NOT NULL REFERENCES agent.tasks(id), generation text NOT NULL,
 request_hash text NOT NULL, status text NOT NULL, attempts integer NOT NULL,
 result jsonb, PRIMARY KEY(task_id,generation,request_hash)
);
