-- Preserve the P2 JSON as historical input; normalized tables are authoritative from P3.
CREATE TABLE business.order_planning (
 order_id text PRIMARY KEY REFERENCES business.orders(id), product_id text NOT NULL,
 as_of date NOT NULL, due_date date NOT NULL,
 local_quantity integer NOT NULL CHECK(local_quantity>=0), local_version integer NOT NULL DEFAULT 1
);
CREATE TABLE business.warehouse_stock (
 order_id text NOT NULL REFERENCES business.orders(id), warehouse_id text NOT NULL,
 available_quantity integer NOT NULL CHECK(available_quantity>=0), version integer NOT NULL DEFAULT 1,
 PRIMARY KEY(order_id,warehouse_id)
);
CREATE TABLE business.transfer_routes (
 order_id text NOT NULL, warehouse_id text NOT NULL,
 transfer_days integer NOT NULL CHECK(transfer_days>=0), unit_cost_cents integer NOT NULL CHECK(unit_cost_cents>=0),
 version integer NOT NULL DEFAULT 1, PRIMARY KEY(order_id,warehouse_id),
 FOREIGN KEY(order_id,warehouse_id) REFERENCES business.warehouse_stock(order_id,warehouse_id)
);
CREATE TABLE business.purchase_batches (
 order_id text NOT NULL REFERENCES business.orders(id), batch_id text NOT NULL,
 quantity integer NOT NULL CHECK(quantity>=0), arrival_date date NOT NULL,
 status text NOT NULL CHECK(status IN ('EXPECTED','CANCELLED')), version integer NOT NULL DEFAULT 1,
 PRIMARY KEY(order_id,batch_id)
);
CREATE TABLE business.customer_policies (
 order_id text PRIMARY KEY REFERENCES business.orders(id), allow_split boolean NOT NULL,
 minimum_first_quantity integer NOT NULL CHECK(minimum_first_quantity>=0),
 last_delivery_date date NOT NULL, version integer NOT NULL DEFAULT 1
);

INSERT INTO business.order_planning
 SELECT o.id,'SKU-DEMO',COALESCE((o.planning_context->>'as_of')::date,(o.updated_at AT TIME ZONE 'Asia/Shanghai')::date),
 COALESCE((o.planning_context->'order'->>'due_date')::date,(o.updated_at AT TIME ZONE 'Asia/Shanghai')::date+o.due_days),
 COALESCE((o.planning_context->>'local_quantity')::integer,s.local_quantity,0),1
 FROM business.orders o LEFT JOIN business.stock s ON s.order_id=o.id;

INSERT INTO business.warehouse_stock
 SELECT o.id,w->>'id',(w->>'available_quantity')::integer,1
 FROM business.orders o LEFT JOIN business.stock s ON s.order_id=o.id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(o.planning_context->'warehouses',
 jsonb_build_array(jsonb_build_object('id','WH-002','available_quantity',COALESCE(s.remote_quantity,0))))) w;
INSERT INTO business.transfer_routes
 SELECT o.id,w->>'id',(w->>'transfer_days')::integer,(w->>'unit_cost_cents')::integer,1
 FROM business.orders o LEFT JOIN business.stock s ON s.order_id=o.id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(o.planning_context->'warehouses',
 jsonb_build_array(jsonb_build_object('id','WH-002','transfer_days',COALESCE(s.transfer_days,0),'unit_cost_cents',200)))) w;
INSERT INTO business.purchase_batches
 SELECT o.id,b->>'id',(b->>'quantity')::integer,(b->>'arrival_date')::date,b->>'status',1
 FROM business.orders o JOIN business.order_planning p ON p.order_id=o.id
 LEFT JOIN business.arrivals a ON a.order_id=o.id
 CROSS JOIN LATERAL jsonb_array_elements(COALESCE(o.planning_context->'purchase_batches',
 jsonb_build_array(jsonb_build_object('id','PO-003','quantity',COALESCE(a.quantity,0),
 'arrival_date',p.as_of+COALESCE(a.arrival_days,0),'status','EXPECTED')))) b;
INSERT INTO business.customer_policies
 SELECT o.id,COALESCE((o.planning_context->'policy'->>'allow_split')::boolean,o.allow_split),
 COALESCE((o.planning_context->'policy'->>'minimum_first_quantity')::integer,60),
 COALESCE((o.planning_context->'policy'->>'last_delivery_date')::date,p.as_of+CASE WHEN o.allow_split THEN 10 ELSE o.due_days END),1
 FROM business.orders o JOIN business.order_planning p ON p.order_id=o.id;
