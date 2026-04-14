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

-- Warehouse schema and daily_orders fact table (producer DAG target).
CREATE SCHEMA IF NOT EXISTS warehouse;

CREATE TABLE IF NOT EXISTS warehouse.daily_orders (
    order_id VARCHAR(64) NOT NULL PRIMARY KEY,
    customer_id VARCHAR(64) NOT NULL,
    order_date DATE NOT NULL,
    amount NUMERIC(12, 2) NOT NULL,
    status VARCHAR(32) NULL
);

CREATE INDEX IF NOT EXISTS idx_daily_orders_order_date ON warehouse.daily_orders (order_date);
