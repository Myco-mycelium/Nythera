#!/bin/bash
# run_tests.sh — Automated CI test runner for Nyrqis
#
# Runs all test suites and generates a summary report.
#
# Usage:
#     ./run_tests.sh                    # Run all tests
#     ./run_tests.sh --quick            # Run quick tests only
#     ./run_tests.sh --gpu              # Run GPU tests only
#     ./run_tests.sh --compositor       # Run compositor tests only

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Counters
TOTAL=0
PASSED=0
FAILED=0
SKIPPED=0

# Parse arguments
QUICK=false
GPU=false
COMPOSITOR=false

for arg in "$@"; do
    case $arg in
        --quick) QUICK=true ;;
        --gpu) GPU=true ;;
        --compositor) COMPOSITOR=true ;;
        --help|-h)
            echo "Usage: $0 [--quick] [--gpu] [--compositor]"
            echo ""
            echo "Options:"
            echo "  --quick        Run quick tests only (no hardware)"
            echo "  --gpu          Run GPU tests only"
            echo "  --compositor   Run compositor tests only"
            exit 0
            ;;
    esac
done

echo ""
echo "============================================================"
echo "  Nyrqis Test Runner"
echo "============================================================"
echo ""
echo "Date: $(date)"
echo "Python: $(python3 --version 2>&1)"
echo ""

# Function to run a test suite
run_test() {
    local name=$1
    local module=$2
    
    echo -n "Running $name... "
    
    output=$(python3 -m unittest "$module" 2>&1)
    exit_code=$?
    
    if [ $exit_code -eq 0 ]; then
        # Parse test count
        count=$(echo "$output" | grep "^Ran" | awk '{print $2}')
        echo -e "${GREEN}OK${NC} ($count tests)"
        PASSED=$((PASSED + 1))
    else
        # Check for failures
        if echo "$output" | grep -q "FAIL:"; then
            echo -e "${RED}FAILED${NC}"
            FAILED=$((FAILED + 1))
        elif echo "$output" | grep -q "SKIP:"; then
            echo -e "${YELLOW}SKIPPED${NC}"
            SKIPPED=$((SKIPPED + 1))
        else
            echo -e "${RED}ERROR${NC}"
            FAILED=$((FAILED + 1))
        fi
    fi
    
    TOTAL=$((TOTAL + 1))
}

# Run tests based on mode
if [ "$QUICK" = true ]; then
    echo "Running quick tests (no hardware required)..."
    echo ""
    run_test "Boot Init" "tests.test_boot_init"
    run_test "Update Signing" "tests.test_update_signing"
    run_test "Wayland Protocol" "tests.test_compositor_integration.TestWaylandProtocol"
    run_test "Wayland Socket" "tests.test_compositor_integration.TestWaylandSocket"
    run_test "Multi-Monitor" "tests.test_render_pipeline.TestMultiMonitor"
    run_test "Output Info" "tests.test_render_pipeline.TestOutputInfo"
    run_test "Hot-Plug Monitor" "tests.test_render_pipeline.TestHotPlugMonitor"
elif [ "$GPU" = true ]; then
    echo "Running GPU tests (requires hardware)..."
    echo ""
    run_test "GBM Hardware" "tests.test_gpu_pipeline.TestGBMRealHardware"
    run_test "EGL Hardware" "tests.test_gpu_pipeline.TestEGLRealHardware"
    run_test "Vulkan Hardware" "tests.test_gpu_pipeline.TestVulkanRealHardware"
    run_test "DRM Hardware" "tests.test_gpu_pipeline.TestDRMRealHardware"
    run_test "Compositor Hardware" "tests.test_gpu_pipeline.TestCompositorRealHardware"
elif [ "$COMPOSITOR" = true ]; then
    echo "Running compositor tests..."
    echo ""
    run_test "Compositor Integration" "tests.test_compositor_integration"
    run_test "Compositor E2E" "tests.test_compositor_e2e"
    run_test "Wayland Client" "tests.test_wayland_client"
    run_test "Full Pipeline" "tests.test_full_pipeline"
else
    echo "Running all tests..."
    echo ""
    # Core tests
    run_test "Boot Init" "tests.test_boot_init"
    run_test "Update Signing" "tests.test_update_signing"
    
    # GPU tests (may skip on systems without hardware)
    run_test "GPU Pipeline" "tests.test_gpu_pipeline"
    
    # Compositor tests
    run_test "Compositor Integration" "tests.test_compositor_integration"
    run_test "Compositor E2E" "tests.test_compositor_e2e"
    run_test "Wayland Client" "tests.test_wayland_client"
    
    # Pipeline tests
    run_test "Render Pipeline" "tests.test_render_pipeline"
    run_test "Full Pipeline" "tests.test_full_pipeline"
fi

# Print summary
echo ""
echo "============================================================"
echo "  Summary"
echo "============================================================"
echo ""
echo "Total:   $TOTAL"
echo -e "Passed:  ${GREEN}$PASSED${NC}"
echo -e "Failed:  ${RED}$FAILED${NC}"
echo -e "Skipped: ${YELLOW}$SKIPPED${NC}"
echo ""

if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}All tests passed!${NC}"
    exit 0
else
    echo -e "${RED}Some tests failed.${NC}"
    exit 1
fi
