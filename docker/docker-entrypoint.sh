#!/bin/bash
set -e

# Function to log the time taken for a specific operation
log_time() {
    local start_time=$1
    local end_time=$2
    local operation_name=$3
    local duration=$((end_time - start_time))
    echo "$operation_name took $duration seconds"
}

# Set default database host if not provided
DB_HOST=${DB_HOST:-db}

# Measure time for waiting for PostgreSQL
start_time_db_wait=$(date +%s)
echo "Waiting for PostgreSQL to be ready at ${DB_HOST}..."
while ! pg_isready -h ${DB_HOST} -U postgres; do
  sleep 1
done
end_time_db_wait=$(date +%s)
log_time $start_time_db_wait $end_time_db_wait "Waiting for PostgreSQL"

echo "PostgreSQL is ready. Initializing database..."

# Measure time for database initialization
start_time_db_init=$(date +%s)
# Initialize database (structure + conditional sample data)
# python docker-init-db.py
end_time_db_init=$(date +%s)
# log_time $start_time_db_init $end_time_db_init "Database initialization"
log_time $start_time_db_init $end_time_db_init  "TODO: Database initialization not yet implemented"

echo "Starting Flask application..."

# Measure time for starting the application
#start_time_app_start=$(date +%s)
end_time_app_start=$(date +%s)
log_time $start_time_db_wait $end_time_app_start "Starting the entire web application"
# Use gunicorn for production or development server for dev
echo "FLASK_ENV ${FLASK_ENV}"
if [ "$FLASK_ENV" = "development" ]; then
    echo "Running in development mode"
    python init.py
else
    echo "Running in production mode"
    gunicorn --bind 0.0.0.0:5000 --workers 4 --timeout 120 init:create_app
fi

