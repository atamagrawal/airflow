-- Licensed to the Apache Software Foundation (ASF) under one
-- or more contributor license agreements.  See the NOTICE file
-- distributed with this work for additional information
-- regarding copyright ownership.  The ASF licenses this file
-- to you under the Apache License, Version 2.0 (the
-- "License"); you may not use this file except in compliance
-- with the License.  You may obtain a copy of the License at
--
--   http://www.apache.org/licenses/LICENSE-2.0
--
-- Unless required by applicable law or agreed to in writing,
-- software distributed under the License is distributed on an
-- "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
-- KIND, either express or implied.  See the License for the
-- specific language governing permissions and limitations
-- under the License.

-- Idempotent daily load: replace rows for the logical date ({{ ds }}).
DELETE FROM warehouse.daily_orders
WHERE order_date = '{{ ds }}'::date;

INSERT INTO warehouse.daily_orders (order_id, customer_id, order_date, amount, status)
VALUES
    ('ord-{{ ds_nodash }}-001', 'cust-001', '{{ ds }}'::date, 99.50, 'shipped'),
    ('ord-{{ ds_nodash }}-002', 'cust-002', '{{ ds }}'::date, 150.00, 'pending'),
    ('ord-{{ ds_nodash }}-003', 'cust-003', '{{ ds }}'::date, 12.25, NULL);
