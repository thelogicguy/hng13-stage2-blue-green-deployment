#!/bin/bash
################################################################################
# Breach Log Viewer - Wrapper Script
#
# This script runs the breach viewer inside the alert_watcher container
# to display error rate threshold breaches captured during chaos testing.
#
# Usage:
#   ./view_breaches.sh              # Show all breaches
#   ./view_breaches.sh --stats      # Show statistics only
#   ./view_breaches.sh --last 10    # Show last 10 breaches
#   ./view_breaches.sh --pool blue  # Show only blue pool breaches
#   ./view_breaches.sh --clear      # Clear breach log
################################################################################

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if alert_watcher container is running
if ! docker ps --format '{{.Names}}' | grep -q '^alert_watcher$'; then
    echo -e "${RED}Error: alert_watcher container is not running${NC}"
    echo "Please start the containers with: docker compose up -d"
    exit 1
fi

# Copy the viewer script into the container (in case it was updated)
docker cp view_breaches.py alert_watcher:/app/view_breaches.py > /dev/null 2>&1

# Run the viewer inside the container with all arguments passed through
docker exec -it alert_watcher python /app/view_breaches.py "$@"
