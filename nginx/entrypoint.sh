#!/bin/sh
set -e

echo "=== Nginx Entrypoint ==="
echo "ACTIVE_POOL: ${ACTIVE_POOL}"
echo "PORT: ${PORT:-3000}"

# Determine the backup pool
if [ "${ACTIVE_POOL}" = "blue" ]; then
    BACKUP_POOL="green"
elif [ "${ACTIVE_POOL}" = "green" ]; then
    BACKUP_POOL="blue"
else
    echo "ERROR: ACTIVE_POOL must be 'blue' or 'green', got: ${ACTIVE_POOL}"
    exit 1
fi

echo "BACKUP_POOL: ${BACKUP_POOL}"

# Use environmen substitue to replace template variables
# Export variables so environment substitue can access them
export ACTIVE_POOL
export BACKUP_POOL
export PORT

# Process the template and output to the actual config file
envsubst '${ACTIVE_POOL} ${BACKUP_POOL} ${PORT}' \
    < /etc/nginx/nginx.conf.template \
    > /etc/nginx/nginx.conf

echo "=== Generated Nginx Config ==="
cat /etc/nginx/nginx.conf

# Test the configuration
nginx -t

# Start Nginx in foreground (Docker best practice)
echo "=== Starting Nginx ==="
exec nginx -g 'daemon off;'