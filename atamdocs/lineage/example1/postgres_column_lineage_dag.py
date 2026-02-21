"""
PostgreSQL Column Lineage DAG Example

This DAG demonstrates how column lineage is automatically tracked for PostgreSQL operations:
1. Creating tables
2. Inserting data
3. Reading and transforming data
4. Writing to another table

The SQLExecuteQueryOperator automatically parses SQL queries and generates column
lineage through OpenLineage integration.

Prerequisites:
- PostgreSQL connection configured in Airflow (connection id: 'postgres_default')
- OpenLineage configured in airflow.cfg
- Local PostgreSQL Docker container running

Setup:
1. Configure Airflow connection:
   airflow connections add 'postgres_default' \
       --conn-type 'postgres' \
       --conn-host 'localhost' \
       --conn-schema 'airflow_db' \
       --conn-login 'airflow' \
       --conn-password 'airflow' \
       --conn-port 5432

2. Enable OpenLineage in airflow.cfg:
   [openlineage]
   namespace = my_airflow_instance
   transport = {"type": "http", "url": "http://localhost:5000"}
"""

from datetime import datetime, timedelta
from airflow import DAG
from airflow.providers.common.sql.operators.sql import SQLExecuteQueryOperator

# Default arguments for the DAG
default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 1,
    'retry_delay': timedelta(minutes=5),
}

with DAG(
    dag_id='postgres_column_lineage_example',
    default_args=default_args,
    description='Demonstrates column lineage tracking for PostgreSQL operations',
    schedule=None,  # Manual trigger
    start_date=datetime(2024, 1, 1),
    catchup=False,
    tags=['postgres', 'column_lineage', 'openlineage', 'example'],
) as dag:

    # Task 1: Create source table (orders)
    # Column lineage: No input columns (table creation)
    create_orders_table = SQLExecuteQueryOperator(
        task_id='create_orders_table',
        conn_id='postgres_default',
        sql="""
            DROP TABLE IF EXISTS orders CASCADE;
            CREATE TABLE orders (
                order_id SERIAL PRIMARY KEY,
                customer_id INTEGER NOT NULL,
                product_name VARCHAR(100),
                order_date DATE NOT NULL,
                amount DECIMAL(10, 2) NOT NULL,
                quantity INTEGER NOT NULL,
                status VARCHAR(20) DEFAULT 'pending'
            );
        """,
        doc_md="""
        ### Create Orders Table
        Creates the source `orders` table with sample schema.

        **Columns:**
        - order_id: Unique identifier for each order
        - customer_id: Customer who placed the order
        - product_name: Name of the product
        - order_date: Date when order was placed
        - amount: Total order amount
        - quantity: Number of items ordered
        - status: Order status
        """,
    )

    # Task 2: Insert sample data into orders table
    # Column lineage: Tracks which literal values populate which columns
    insert_orders_data = SQLExecuteQueryOperator(
        task_id='insert_orders_data',
        conn_id='postgres_default',
        sql="""
            INSERT INTO orders (customer_id, product_name, order_date, amount, quantity, status)
            VALUES
                (101, 'Laptop', '2024-01-15', 1200.00, 1, 'completed'),
                (102, 'Mouse', '2024-01-16', 25.50, 2, 'completed'),
                (101, 'Keyboard', '2024-01-16', 75.00, 1, 'completed'),
                (103, 'Monitor', '2024-01-17', 350.00, 1, 'pending'),
                (102, 'Laptop', '2024-01-18', 1200.00, 1, 'completed'),
                (104, 'Headphones', '2024-01-18', 150.00, 1, 'shipped'),
                (103, 'Mouse', '2024-01-19', 25.50, 3, 'completed'),
                (105, 'Keyboard', '2024-01-20', 75.00, 2, 'completed'),
                (104, 'Monitor', '2024-01-20', 350.00, 1, 'shipped'),
                (105, 'Laptop', '2024-01-21', 1200.00, 1, 'pending');
        """,
        doc_md="""
        ### Insert Sample Orders
        Inserts 10 sample orders into the orders table.
        """,
    )

    # Task 3: Create customers table
    create_customers_table = SQLExecuteQueryOperator(
        task_id='create_customers_table',
        conn_id='postgres_default',
        sql="""
            DROP TABLE IF EXISTS customers CASCADE;
            CREATE TABLE customers (
                customer_id INTEGER PRIMARY KEY,
                first_name VARCHAR(50),
                last_name VARCHAR(50),
                email VARCHAR(100),
                signup_date DATE
            );
        """,
        doc_md="""
        ### Create Customers Table
        Creates the `customers` table to store customer information.
        """,
    )

    # Task 4: Insert customer data
    insert_customers_data = SQLExecuteQueryOperator(
        task_id='insert_customers_data',
        conn_id='postgres_default',
        sql="""
            INSERT INTO customers (customer_id, first_name, last_name, email, signup_date)
            VALUES
                (101, 'John', 'Doe', 'john.doe@example.com', '2023-12-01'),
                (102, 'Jane', 'Smith', 'jane.smith@example.com', '2023-12-05'),
                (103, 'Bob', 'Johnson', 'bob.johnson@example.com', '2023-12-10'),
                (104, 'Alice', 'Williams', 'alice.williams@example.com', '2023-12-15'),
                (105, 'Charlie', 'Brown', 'charlie.brown@example.com', '2023-12-20');
        """,
        doc_md="""
        ### Insert Sample Customers
        Inserts 5 sample customers into the customers table.
        """,
    )

    # Task 5: Create aggregated summary table with JOIN
    # Column lineage: Automatically tracks:
    # - customer_summary.customer_id <- orders.customer_id
    # - customer_summary.customer_name <- customers.first_name, customers.last_name
    # - customer_summary.customer_email <- customers.email
    # - customer_summary.total_orders <- COUNT(orders.order_id)
    # - customer_summary.total_spent <- SUM(orders.amount)
    # - customer_summary.avg_order_value <- AVG(orders.amount)
    # - customer_summary.first_order_date <- MIN(orders.order_date)
    # - customer_summary.last_order_date <- MAX(orders.order_date)
    create_customer_summary = SQLExecuteQueryOperator(
        task_id='create_customer_summary',
        conn_id='postgres_default',
        sql="""
            DROP TABLE IF EXISTS customer_summary CASCADE;

            CREATE TABLE customer_summary AS
            SELECT
                c.customer_id,
                CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
                c.email AS customer_email,
                COUNT(o.order_id) AS total_orders,
                SUM(o.amount) AS total_spent,
                AVG(o.amount) AS avg_order_value,
                MIN(o.order_date) AS first_order_date,
                MAX(o.order_date) AS last_order_date
            FROM customers c
            LEFT JOIN orders o ON c.customer_id = o.customer_id
            GROUP BY c.customer_id, c.first_name, c.last_name, c.email;
        """,
        doc_md="""
        ### Create Customer Summary (with JOIN and Aggregations)

        Creates an aggregated view of customer orders by joining orders and customers tables.

        **Column Lineage Tracked:**
        - `customer_id` ← `customers.customer_id`
        - `customer_name` ← `customers.first_name`, `customers.last_name` (CONCAT)
        - `customer_email` ← `customers.email`
        - `total_orders` ← COUNT(`orders.order_id`)
        - `total_spent` ← SUM(`orders.amount`)
        - `avg_order_value` ← AVG(`orders.amount`)
        - `first_order_date` ← MIN(`orders.order_date`)
        - `last_order_date` ← MAX(`orders.order_date`)

        **Transformation Types:**
        - IDENTITY: customer_id, customer_email
        - CUSTOM: customer_name (concatenation)
        - AGGREGATE: total_orders, total_spent, avg_order_value, first_order_date, last_order_date
        """,
    )

    # Task 6: Create daily orders summary
    # Column lineage: Automatically tracks:
    # - daily_orders.order_date <- orders.order_date (DATE function)
    # - daily_orders.total_orders <- COUNT(orders.order_id)
    # - daily_orders.total_revenue <- SUM(orders.amount)
    # - daily_orders.avg_order_value <- AVG(orders.amount)
    # - daily_orders.total_quantity <- SUM(orders.quantity)
    create_daily_summary = SQLExecuteQueryOperator(
        task_id='create_daily_summary',
        conn_id='postgres_default',
        sql="""
            DROP TABLE IF EXISTS daily_orders CASCADE;

            CREATE TABLE daily_orders AS
            SELECT
                DATE(order_date) AS order_date,
                COUNT(order_id) AS total_orders,
                SUM(amount) AS total_revenue,
                AVG(amount) AS avg_order_value,
                SUM(quantity) AS total_quantity
            FROM orders
            GROUP BY DATE(order_date)
            ORDER BY order_date;
        """,
        doc_md="""
        ### Create Daily Orders Summary

        Aggregates orders by date to show daily metrics.

        **Column Lineage Tracked:**
        - `order_date` ← `orders.order_date` (DATE transformation)
        - `total_orders` ← COUNT(`orders.order_id`)
        - `total_revenue` ← SUM(`orders.amount`)
        - `avg_order_value` ← AVG(`orders.amount`)
        - `total_quantity` ← SUM(`orders.quantity`)

        **Transformation Types:**
        - TRANSFORM: order_date (DATE function)
        - AGGREGATE: total_orders, total_revenue, avg_order_value, total_quantity
        """,
    )

    # Task 7: Create product summary with complex transformations
    # Column lineage: Tracks complex transformations including CASE statements
    create_product_summary = SQLExecuteQueryOperator(
        task_id='create_product_summary',
        conn_id='postgres_default',
        sql="""
            DROP TABLE IF EXISTS product_summary CASCADE;

            CREATE TABLE product_summary AS
            SELECT
                product_name,
                COUNT(order_id) AS order_count,
                SUM(amount) AS total_revenue,
                SUM(quantity) AS total_quantity,
                AVG(amount) AS avg_price,
                CASE
                    WHEN SUM(amount) > 2000 THEN 'High Revenue'
                    WHEN SUM(amount) > 500 THEN 'Medium Revenue'
                    ELSE 'Low Revenue'
                END AS revenue_category,
                CASE
                    WHEN COUNT(order_id) > 3 THEN 'Popular'
                    ELSE 'Standard'
                END AS popularity_tier
            FROM orders
            GROUP BY product_name
            ORDER BY total_revenue DESC;
        """,
        doc_md="""
        ### Create Product Summary (with CASE statements)

        Aggregates orders by product with conditional categorization.

        **Column Lineage Tracked:**
        - `product_name` ← `orders.product_name`
        - `order_count` ← COUNT(`orders.order_id`)
        - `total_revenue` ← SUM(`orders.amount`)
        - `total_quantity` ← SUM(`orders.quantity`)
        - `avg_price` ← AVG(`orders.amount`)
        - `revenue_category` ← CASE on SUM(`orders.amount`)
        - `popularity_tier` ← CASE on COUNT(`orders.order_id`)

        **Transformation Types:**
        - IDENTITY: product_name
        - AGGREGATE: order_count, total_revenue, total_quantity, avg_price
        - CUSTOM: revenue_category, popularity_tier (conditional logic)
        """,
    )

    # Task 8: Insert additional data into an existing table
    # Column lineage: Tracks INSERT INTO with SELECT
    insert_high_value_orders = SQLExecuteQueryOperator(
        task_id='insert_high_value_orders',
        conn_id='postgres_default',
        sql="""
            DROP TABLE IF EXISTS high_value_orders CASCADE;

            CREATE TABLE high_value_orders (
                order_id INTEGER,
                customer_id INTEGER,
                product_name VARCHAR(100),
                amount DECIMAL(10, 2),
                order_date DATE,
                value_tier VARCHAR(20)
            );

            INSERT INTO high_value_orders (order_id, customer_id, product_name, amount, order_date, value_tier)
            SELECT
                order_id,
                customer_id,
                product_name,
                amount,
                order_date,
                CASE
                    WHEN amount > 1000 THEN 'Premium'
                    WHEN amount > 100 THEN 'Standard'
                    ELSE 'Budget'
                END AS value_tier
            FROM orders
            WHERE amount > 100;
        """,
        doc_md="""
        ### Insert High-Value Orders (INSERT INTO ... SELECT)

        Filters and transforms orders data into a new table.

        **Column Lineage Tracked:**
        - `high_value_orders.order_id` ← `orders.order_id`
        - `high_value_orders.customer_id` ← `orders.customer_id`
        - `high_value_orders.product_name` ← `orders.product_name`
        - `high_value_orders.amount` ← `orders.amount`
        - `high_value_orders.order_date` ← `orders.order_date`
        - `high_value_orders.value_tier` ← CASE on `orders.amount`

        **Transformation Types:**
        - IDENTITY: order_id, customer_id, product_name, amount, order_date
        - CUSTOM: value_tier (conditional categorization)
        """,
    )

    # Task 9: Complex multi-table transformation
    # Column lineage: Tracks lineage across multiple source tables with complex JOIN
    create_customer_product_matrix = SQLExecuteQueryOperator(
        task_id='create_customer_product_matrix',
        conn_id='postgres_default',
        sql="""
            DROP TABLE IF EXISTS customer_product_matrix CASCADE;

            CREATE TABLE customer_product_matrix AS
            SELECT
                c.customer_id,
                CONCAT(c.first_name, ' ', c.last_name) AS customer_name,
                o.product_name,
                COUNT(o.order_id) AS purchase_count,
                SUM(o.amount) AS total_spent_on_product,
                AVG(o.amount) AS avg_price_paid,
                MIN(o.order_date) AS first_purchase_date,
                MAX(o.order_date) AS last_purchase_date,
                CASE
                    WHEN COUNT(o.order_id) > 1 THEN 'Repeat Buyer'
                    ELSE 'One-time Buyer'
                END AS buyer_type
            FROM customers c
            INNER JOIN orders o ON c.customer_id = o.customer_id
            GROUP BY c.customer_id, c.first_name, c.last_name, o.product_name
            ORDER BY c.customer_id, total_spent_on_product DESC;
        """,
        doc_md="""
        ### Create Customer-Product Matrix (Complex Multi-table JOIN)

        Creates a matrix showing each customer's purchase history per product.

        **Column Lineage Tracked:**
        - `customer_id` ← `customers.customer_id`
        - `customer_name` ← `customers.first_name`, `customers.last_name`
        - `product_name` ← `orders.product_name`
        - `purchase_count` ← COUNT(`orders.order_id`)
        - `total_spent_on_product` ← SUM(`orders.amount`)
        - `avg_price_paid` ← AVG(`orders.amount`)
        - `first_purchase_date` ← MIN(`orders.order_date`)
        - `last_purchase_date` ← MAX(`orders.order_date`)
        - `buyer_type` ← CASE on COUNT(`orders.order_id`)

        **Transformation Types:**
        - IDENTITY: customer_id, product_name
        - CUSTOM: customer_name (concatenation), buyer_type (conditional)
        - AGGREGATE: purchase_count, total_spent_on_product, avg_price_paid,
                    first_purchase_date, last_purchase_date
        """,
    )

    # Task 10: Read and verify data (SELECT query)
    # This task demonstrates that SELECT queries also generate lineage
    verify_data = SQLExecuteQueryOperator(
        task_id='verify_data',
        conn_id='postgres_default',
        sql="""
            SELECT 'Orders' AS table_name, COUNT(*) AS row_count FROM orders
            UNION ALL
            SELECT 'Customers', COUNT(*) FROM customers
            UNION ALL
            SELECT 'Customer Summary', COUNT(*) FROM customer_summary
            UNION ALL
            SELECT 'Daily Orders', COUNT(*) FROM daily_orders
            UNION ALL
            SELECT 'Product Summary', COUNT(*) FROM product_summary
            UNION ALL
            SELECT 'High Value Orders', COUNT(*) FROM high_value_orders
            UNION ALL
            SELECT 'Customer Product Matrix', COUNT(*) FROM customer_product_matrix;
        """,
        doc_md="""
        ### Verify Data

        Queries all created tables to verify data was loaded correctly.
        Even SELECT queries generate column lineage information.
        """,
    )

    # Define task dependencies
    # Setup phase: Create and populate base tables
    create_orders_table >> insert_orders_data
    create_customers_table >> insert_customers_data

    # Transformation phase: Create derived tables
    # These depend on base tables being populated
    [insert_orders_data, insert_customers_data] >> create_customer_summary
    insert_orders_data >> create_daily_summary
    insert_orders_data >> create_product_summary
    insert_orders_data >> insert_high_value_orders
    [insert_orders_data, insert_customers_data] >> create_customer_product_matrix

    # Verification phase: Check all tables
    [
        create_customer_summary,
        create_daily_summary,
        create_product_summary,
        insert_high_value_orders,
        create_customer_product_matrix,
    ] >> verify_data
