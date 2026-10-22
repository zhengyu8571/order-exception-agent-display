CREATE SCHEMA IF NOT EXISTS business;
CREATE TABLE IF NOT EXISTS business.orders (
 id text PRIMARY KEY, customer text NOT NULL, required_quantity integer NOT NULL CHECK(required_quantity > 0),
 due_days integer NOT NULL, allow_split boolean NOT NULL, version integer NOT NULL DEFAULT 1,
 status text NOT NULL DEFAULT 'EXCEPTION', delivery_plan jsonb,
 updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS business.stock (
 order_id text PRIMARY KEY REFERENCES business.orders(id),
 local_quantity integer NOT NULL, remote_quantity integer NOT NULL, transfer_days integer NOT NULL
);
CREATE TABLE IF NOT EXISTS business.arrivals (
 order_id text PRIMARY KEY REFERENCES business.orders(id), quantity integer NOT NULL, arrival_days integer NOT NULL
);
CREATE TABLE IF NOT EXISTS business.operations (
 operation_id text PRIMARY KEY, order_id text NOT NULL REFERENCES business.orders(id),
 payload_hash text NOT NULL, result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
