#!/bin/bash

################################################################################
# Blue-Green Deployment Alert Testing Script
#
# This script automates the testing of the observability and alerting system.
# It performs the following tests:
# 1. Baseline verification (blue serving traffic)
# 2. Failover test (blue -> green)
# 3. Error rate threshold test
# 4. Recovery test (green -> blue)
# 5. Maintenance mode test
#
# Usage: ./test_alerts.sh
################################################################################

set -e  # Exit on error

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Configuration
NGINX_URL="http://localhost:8080"
BLUE_URL="http://localhost:8081"
GREEN_URL="http://localhost:8082"
SLEEP_BETWEEN_TESTS=10

################################################################################
# Helper Functions
################################################################################

print_header() {
    echo ""
    echo -e "${CYAN}============================================================================${NC}"
    echo -e "${CYAN}$1${NC}"
    echo -e "${CYAN}============================================================================${NC}"
    echo ""
}

print_step() {
    echo -e "${BLUE}▶ $1${NC}"
}

print_success() {
    echo -e "${GREEN}✔ $1${NC}"
}

print_error() {
    echo -e "${RED}✖ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

wait_with_countdown() {
    local seconds=$1
    local message=$2
    echo -e "${YELLOW}⏳ $message${NC}"
    for ((i=$seconds; i>0; i--)); do
        echo -ne "   Waiting ${i}s...\r"
        sleep 1
    done
    echo -e "   ${GREEN}Ready!${NC}        "
}

check_service_health() {
    local url=$1
    local service_name=$2

    if curl -sf "$url/healthz" > /dev/null 2>&1; then
        print_success "$service_name is healthy"
        return 0
    else
        print_error "$service_name is not healthy"
        return 1
    fi
}

get_serving_pool() {
    curl -si "$NGINX_URL/version" 2>/dev/null | grep -i "X-App-Pool:" | awk '{print $2}' | tr -d '\r'
}

################################################################################
# Test Functions
################################################################################

test_baseline() {
    print_header "TEST 1: Baseline Verification"

    print_step "Checking all services are running..."
    docker compose ps
    echo ""

    print_step "Checking service health..."
    check_service_health "$NGINX_URL" "Nginx"
    check_service_health "$BLUE_URL" "Blue Pool"
    check_service_health "$GREEN_URL" "Green Pool"
    echo ""

    print_step "Verifying blue pool is serving traffic..."
    local pool=$(get_serving_pool)
    if [ "$pool" = "blue" ]; then
        print_success "Blue pool is serving traffic (expected)"
        echo ""
        curl -i "$NGINX_URL/version" | grep -E "(X-App-Pool|X-Release-Id)"
    else
        print_error "Expected blue pool, but got: $pool"
        exit 1
    fi

    print_success "Baseline verification passed"
}

test_failover() {
    print_header "TEST 2: Failover Detection (Blue → Green)"

    print_step "Triggering chaos mode on blue pool (error mode)..."
    curl -X POST "$BLUE_URL/chaos/start?mode=error" 2>/dev/null
    print_success "Chaos mode enabled on blue pool"
    echo ""

    wait_with_countdown 5 "Allowing Nginx to detect failures..."
    echo ""

    print_step "Sending requests through Nginx to trigger failover..."
    for i in {1..10}; do
        curl -s "$NGINX_URL/version" > /dev/null
        echo -n "."
    done
    echo ""
    echo ""

    print_step "Verifying traffic has failed over to green pool..."
    local pool=$(get_serving_pool)
    if [ "$pool" = "green" ]; then
        print_success "Failover successful! Green pool is now serving traffic"
        echo ""
        curl -i "$NGINX_URL/version" | grep -E "(X-App-Pool|X-Release-Id)"
    else
        print_error "Failover failed! Expected green pool, but got: $pool"
        # Don't exit, continue with tests
    fi

    echo ""
    print_warning "CHECK SLACK: You should see a FAILOVER DETECTED alert"
    print_warning "Alert should show: blue → green"
    echo ""

    print_success "Failover test completed"
}

test_error_rate() {
    print_header "TEST 3: Error Rate Threshold Detection"

    print_step "Green pool is currently serving traffic"
    print_step "Enabling chaos mode on green to generate 5xx errors..."
    curl -X POST "$GREEN_URL/chaos/start?mode=error" 2>/dev/null
    print_success "Chaos mode enabled on green pool"
    echo ""

    print_step "Sending 100 requests to build up error window..."
    local error_count=0
    for i in {1..100}; do
        response=$(curl -s -o /dev/null -w "%{http_code}" "$NGINX_URL/version")
        if [[ $response == 5* ]]; then
            ((error_count++))
            echo -n "E"
        else
            echo -n "."
        fi
        sleep 0.1
    done
    echo ""
    echo ""

    local error_rate=$((error_count))
    print_step "Generated $error_count errors out of 100 requests"

    if [ $error_count -gt 2 ]; then
        print_success "Error rate threshold likely exceeded"
    else
        print_warning "Error rate may not exceed threshold (only $error_count errors)"
    fi

    echo ""
    print_warning "CHECK SLACK: You should see a HIGH ERROR RATE alert"
    print_warning "Alert should show error rate > 2% threshold"
    echo ""

    print_success "Error rate test completed"
}

test_recovery() {
    print_header "TEST 4: Recovery Detection (Return to Blue)"

    print_step "Stopping chaos mode on blue pool to allow recovery..."
    curl -X POST "$BLUE_URL/chaos/stop" 2>/dev/null
    print_success "Chaos mode disabled on blue pool"
    echo ""

    wait_with_countdown 8 "Waiting for blue pool to recover and Nginx to detect it..."
    echo ""

    print_step "Sending requests to trigger recovery..."
    for i in {1..10}; do
        curl -s "$NGINX_URL/version" > /dev/null
        echo -n "."
        sleep 0.5
    done
    echo ""
    echo ""

    print_step "Verifying traffic has returned to blue pool..."
    local pool=$(get_serving_pool)
    if [ "$pool" = "blue" ]; then
        print_success "Recovery successful! Blue pool is serving traffic again"
        echo ""
        curl -i "$NGINX_URL/version" | grep -E "(X-App-Pool|X-Release-Id)"
    else
        print_error "Recovery not detected! Expected blue pool, but got: $pool"
    fi

    echo ""
    print_warning "CHECK SLACK: You should see a RECOVERY DETECTED alert"
    print_warning "Alert should show: blue pool recovered"
    echo ""

    # Clean up - stop chaos on green too
    print_step "Cleaning up: stopping chaos mode on green pool..."
    curl -X POST "$GREEN_URL/chaos/stop" 2>/dev/null
    print_success "Chaos mode disabled on green pool"

    print_success "Recovery test completed"
}

test_maintenance_mode() {
    print_header "TEST 5: Maintenance Mode Verification"

    print_step "Enabling maintenance mode..."
    docker exec alert_watcher touch /app/state/maintenance.flag
    print_success "Maintenance flag created"
    echo ""

    wait_with_countdown 3 "Waiting for watcher to detect maintenance mode..."
    echo ""

    print_step "Checking watcher logs for maintenance mode confirmation..."
    docker logs alert_watcher 2>&1 | tail -5 | grep -i maintenance || true
    echo ""

    print_step "Triggering a failover event (should be suppressed)..."
    curl -X POST "$BLUE_URL/chaos/start?mode=error" 2>/dev/null
    for i in {1..5}; do
        curl -s "$NGINX_URL/version" > /dev/null
        echo -n "."
    done
    echo ""
    echo ""

    print_warning "CHECK SLACK: You should NOT see any alerts (maintenance mode active)"
    echo ""

    print_step "Disabling maintenance mode..."
    docker exec alert_watcher rm -f /app/state/maintenance.flag
    docker compose restart alert_watcher > /dev/null 2>&1
    print_success "Maintenance mode disabled"
    echo ""

    print_step "Cleaning up: stopping chaos mode..."
    curl -X POST "$BLUE_URL/chaos/stop" 2>/dev/null
    print_success "Chaos mode disabled"

    print_success "Maintenance mode test completed"
}

view_logs() {
    print_header "Service Logs"

    echo -e "${BLUE}▶ Alert Watcher Logs (last 30 lines):${NC}"
    echo "─────────────────────────────────────"
    docker logs --tail 30 alert_watcher 2>&1
    echo ""

    echo -e "${BLUE}▶ Nginx Access Logs (last 10 lines):${NC}"
    echo "─────────────────────────────────────"
    docker exec nginx_proxy tail -10 /var/log/nginx/access.log 2>&1
    echo ""
}

################################################################################
# Main Execution
################################################################################

main() {
    clear

    print_header "🔍 BLUE-GREEN DEPLOYMENT ALERT TESTING SUITE"

    echo -e "${YELLOW}This script will test the observability and alerting system.${NC}"
    echo -e "${YELLOW}Make sure you have configured SLACK_WEBHOOK_URL in your .env file.${NC}"
    echo ""
    echo "The following tests will be performed:"
    echo "  1. Baseline verification"
    echo "  2. Failover detection (blue → green)"
    echo "  3. Error rate threshold detection"
    echo "  4. Recovery detection (return to blue)"
    echo "  5. Maintenance mode suppression"
    echo ""

    read -p "Press ENTER to start testing or Ctrl+C to cancel..."
    echo ""

    # Run all tests
    test_baseline
    wait_with_countdown $SLEEP_BETWEEN_TESTS "Waiting before next test..."

    test_failover
    wait_with_countdown $SLEEP_BETWEEN_TESTS "Waiting before next test..."

    test_error_rate
    wait_with_countdown $SLEEP_BETWEEN_TESTS "Waiting before next test..."

    test_recovery
    wait_with_countdown $SLEEP_BETWEEN_TESTS "Waiting before next test..."

    test_maintenance_mode
    echo ""

    # Show logs
    view_logs

    # Final summary
    print_header "✅ ALL TESTS COMPLETED"

    echo -e "${GREEN}Test suite finished successfully!${NC}"
    echo ""
    echo "Expected Slack Alerts:"
    echo "  🔄 Failover detected (blue → green)"
    echo "  🚨 High error rate detected"
    echo "  ✅ Recovery detected (blue recovered)"
    echo ""
    echo "Please verify these alerts in your Slack channel."
    echo ""
    echo "To view real-time logs:"
    echo "  docker logs -f alert_watcher"
    echo "  docker exec nginx_proxy tail -f /var/log/nginx/access.log"
    echo ""
}

# Run main function
main
