#!/bin/bash
set -e

echo ""
echo "================================"

# Function to log the time taken for a specific operation
log_time() {
    local start_time=$1
    local end_time=$2
    local operation_name=$3
    local duration=$((end_time - start_time))
    echo "$operation_name took $duration seconds"
}

# Validate FLASK_ENV
case "${FLASK_ENV}" in
  development|test|production|demo) ;;
  *)
    echo "ERROR: FLASK_ENV must be one of: development, test, production, demo (got: '${FLASK_ENV}')"
    exit 1
    ;;
esac

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

start_time_db_init=$(date +%s)
python docker-init-db.py
end_time_db_init=$(date +%s)
log_time $start_time_db_init $end_time_db_init "Database initialization"

echo "Starting Flask application..."

end_time_app_start=$(date +%s)
log_time $start_time_db_wait $end_time_app_start "Starting the entire web application"

if [ "$FLASK_ENV" = "development" ]; then
    echo "Running in development mode straight with 'python init.py'"
    python init.py
else
    echo "Running in ${FLASK_ENV} mode with gunicorn"
    gunicorn --bind 0.0.0.0:5000 --workers 4 --timeout 120 wsgi:app
fi
