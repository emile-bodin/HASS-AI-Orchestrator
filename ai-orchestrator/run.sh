#!/usr/bin/env bash
set -e

echo "=========================================="
echo "AI Orchestrator - Starting up"
echo "=========================================="

# Parse add-on configuration
CONFIG_PATH="${CONFIG_PATH:-/data/options.json}"
DATA_DIR="${DATA_DIR:-/data}"
APP_DIR="${APP_DIR:-/app}"
ADDON_CONFIG_DIR="${ADDON_CONFIG_DIR:-/config}"
OLLAMA_PID=""
API_PID=""
ACTIVE_PID=""
cleanup() {
    trap '' TERM INT
    local pid n alive
    for pid in "$ACTIVE_PID" "$API_PID" "$OLLAMA_PID"; do
        if [ -n "$pid" ]; then kill -TERM "$pid" 2>/dev/null || true; fi
    done
    # Bound graceful draining, then reap only children started by this script.
    for ((n=0; n<50; n++)); do
        alive=false
        for pid in "$ACTIVE_PID" "$API_PID" "$OLLAMA_PID"; do
            if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then alive=true; fi
        done
        [ "$alive" = false ] && break
        sleep 0.1
    done
    for pid in "$ACTIVE_PID" "$API_PID" "$OLLAMA_PID"; do
        if [ -n "$pid" ]; then kill -KILL "$pid" 2>/dev/null || true; wait "$pid" 2>/dev/null || true; fi
    done
}
trap cleanup EXIT
trap 'exit 0' TERM INT

if [ ! -f "$CONFIG_PATH" ]; then
    echo "ERROR: Configuration file not found at $CONFIG_PATH"
    exit 1
fi

# Extract configuration values
export OLLAMA_HOST=$(jq -r '.ollama_host // "http://localhost:11434"' "$CONFIG_PATH")
export DRY_RUN_MODE=$(jq -r 'if .dry_run_mode == null then true else .dry_run_mode end' "$CONFIG_PATH")
export LOG_LEVEL=$(jq -r '.log_level // "info"' "$CONFIG_PATH" | tr '[:lower:]' '[:upper:]')
export ORCHESTRATOR_MODEL=$(jq -r '.orchestrator_model // "gemma4:e4b"' "$CONFIG_PATH")
export SMART_MODEL=$(jq -r '.smart_model // "gemma4:e4b"' "$CONFIG_PATH")
export FAST_MODEL=$(jq -r '.fast_model // "gemma4:e4b"' "$CONFIG_PATH")
export DEEP_REASONING_MODEL=$(jq -r '.deep_reasoning_model // "gemma4:e4b"' "$CONFIG_PATH")
export REASONING_DEFAULT_PROFILE=$(jq -r '.reasoning_default_profile // "balanced"' "$CONFIG_PATH")
export DECISION_INTERVAL=$(jq -r '.decision_interval // 120' "$CONFIG_PATH")
export ENABLE_GPU=$(jq -r '.enable_gpu // false' "$CONFIG_PATH")
export ENABLE_RAG=$(jq -r 'if .enable_rag == null then true else .enable_rag end' "$CONFIG_PATH")
export LLM_PROVIDER=$(jq -r '.llm_provider // "ollama"' "$CONFIG_PATH")
export OPENAI_API_KEY=$(jq -r '.openai_api_key // ""' "$CONFIG_PATH")
export OPENAI_BASE_URL=$(jq -r '.openai_base_url // ""' "$CONFIG_PATH")
export OPENAI_MODEL=$(jq -r '.openai_model // "gpt-5.6-terra"' "$CONFIG_PATH")
# Effective generation roles only; leave saved local models and RAG untouched.
if [ "$LLM_PROVIDER" = "openai_oauth" ]; then
    export ORCHESTRATOR_MODEL="$OPENAI_MODEL"
    export SMART_MODEL="$OPENAI_MODEL"
    export FAST_MODEL="$OPENAI_MODEL"
    export DEEP_REASONING_MODEL="$OPENAI_MODEL"
fi
export OPENAI_OAUTH_REDIRECT_URI=$(jq -r '.openai_oauth_redirect_uri // ""' "$CONFIG_PATH")
export OPENAI_OAUTH_AGENT_NAME=$(jq -r '.openai_oauth_agent_name // "HASS-AI-Orchestrator"' "$CONFIG_PATH")
export ANTHROPIC_API_KEY=$(jq -r '.anthropic_api_key // ""' "$CONFIG_PATH")
export ANTHROPIC_MODEL=$(jq -r '.anthropic_model // "claude-opus-4-8"' "$CONFIG_PATH")
export REASONING_EFFORT=$(jq -r '.reasoning_effort // "medium"' "$CONFIG_PATH")
export REASONING_MAX_TOOL_CALLS=$(jq -r '.reasoning_max_tool_calls // 30' "$CONFIG_PATH")
export REASONING_MAX_SECONDS=$(jq -r '.reasoning_max_seconds // 180' "$CONFIG_PATH")
export REASONING_LLM_TIMEOUT=$(jq -r '.reasoning_llm_timeout // 120' "$CONFIG_PATH")
export REASONING_TOOL_TIMEOUT=$(jq -r '.reasoning_tool_timeout // 30' "$CONFIG_PATH")
export REASONING_MAX_CONCURRENT_RUNS=$(jq -r '.reasoning_max_concurrent_runs // 1' "$CONFIG_PATH")

# Security Controls
export ALLOWED_DOMAINS=$(jq -r '.allowed_domains // ""' "$CONFIG_PATH")
export BLOCKED_DOMAINS=$(jq -r '.blocked_domains // ""' "$CONFIG_PATH")
export HIGH_IMPACT_SERVICES=$(jq -r '.high_impact_services // ""' "$CONFIG_PATH")
export MIN_TEMP=$(jq -r '.min_temp // 10.0' "$CONFIG_PATH")
export MAX_TEMP=$(jq -r '.max_temp // 30.0' "$CONFIG_PATH")
export MAX_TEMP_CHANGE=$(jq -r '.max_temp_change // 3.0' "$CONFIG_PATH")
export GEMINI_API_KEY=$(jq -r '.gemini_api_key // ""' "$CONFIG_PATH")
export USE_GEMINI_FOR_DASHBOARD=$(jq -r '.use_gemini_for_dashboard // false' "$CONFIG_PATH")
export GEMINI_MODEL_NAME=$(jq -r '.gemini_model_name // "gemini-3.5-flash"' "$CONFIG_PATH")

# Home Assistant API configuration
# Home Assistant API configuration
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN}"
export HA_ACCESS_TOKEN=$(jq -r '.ha_access_token // ""' "$CONFIG_PATH")
export HA_URL=$(jq -r '.ha_url // ""' "$CONFIG_PATH")

echo "Configuration loaded:"
echo "  Ollama Host: $OLLAMA_HOST"
echo "  Dry Run Mode: $DRY_RUN_MODE"
echo "  Log Level: $LOG_LEVEL"
echo "  Orchestrator Model: $ORCHESTRATOR_MODEL"
echo "  Smart Model: $SMART_MODEL"
echo "  Fast Model: $FAST_MODEL"
echo "  Deep Reasoning Model: $DEEP_REASONING_MODEL"
echo "  Default Reasoning Profile: $REASONING_DEFAULT_PROFILE"
echo "  Decision Interval: ${DECISION_INTERVAL}s"
echo "  GPU Enabled: $ENABLE_GPU"
echo "  RAG Enabled: $ENABLE_RAG"
echo "  LLM Provider: $LLM_PROVIDER"
if [ "$ENABLE_RAG" = "true" ] && [ "$LLM_PROVIDER" != "ollama" ]; then
    echo "  RAG uses local Ollama embeddings independently of cloud generation"
fi
if [ -n "$HA_ACCESS_TOKEN" ]; then
    echo "  HA Access Token: PROVIDED (Length: ${#HA_ACCESS_TOKEN})"
    if [ -z "$HA_URL" ]; then
        echo "  > Direct Core access will use the internal Home Assistant hostname"
    else
        echo "  > Using custom HA URL: $HA_URL"
    fi
else
    echo "  HA Access Token: NOT PROVIDED (Using Supervisor Token fallback)"
fi

# Start Ollama server only when selected as managed (legacy default migrates).
OLLAMA_MODE=$(jq -r '.ollama_mode // "auto"' "$CONFIG_PATH")
INTERNAL_OLLAMA_PORT=$(jq -r '.internal_ollama_port // 11435' "$CONFIG_PATH")
case "$OLLAMA_MODE" in
    auto)
        case "$OLLAMA_HOST" in
            http://localhost:11434|http://localhost:11434/|http://127.0.0.1:11434|http://127.0.0.1:11434/) OLLAMA_MODE=internal ;;
            *) OLLAMA_MODE=external ;;
        esac ;;
    internal|external) ;;
    *) echo "ERROR: ollama_mode must be auto, internal, or external"; exit 1 ;;
esac
if [ "$OLLAMA_MODE" = internal ]; then
    if ! [[ "$INTERNAL_OLLAMA_PORT" =~ ^[0-9]+$ ]] || ((INTERNAL_OLLAMA_PORT < 1024 || INTERNAL_OLLAMA_PORT > 65535)); then
        echo "ERROR: internal_ollama_port must be between 1024 and 65535"; exit 1
    fi
    export OLLAMA_HOST="http://127.0.0.1:$INTERNAL_OLLAMA_PORT"
fi
echo "Effective Ollama target: $OLLAMA_HOST ($OLLAMA_MODE)"
if [ "$OLLAMA_MODE" = internal ] \
    && { [[ "$LLM_PROVIDER" == "ollama" ]] || [[ "$ENABLE_RAG" == "true" ]]; }; then
    echo "=========================================="
    echo "Starting Ollama server..."
    echo "=========================================="
    
    # Set GPU support if enabled
    if [ "$ENABLE_GPU" = "true" ]; then
        export OLLAMA_GPU=1
        echo "GPU support enabled"
    fi
    
    # Do not reuse or terminate any existing host-network listener.
    python3 - "$INTERNAL_OLLAMA_PORT" <<'PY'
import socket
import sys
try:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", int(sys.argv[1])))
except OSError as exc:
    sys.exit(f"ERROR: Managed Ollama port {sys.argv[1]} unavailable: {exc}; refusing to reuse another server")
PY
    export HOME="$DATA_DIR/ollama/home"
    export OLLAMA_MODELS="$DATA_DIR/ollama/models"
    export OLLAMA_KV_CACHE_TYPE=f16
    mkdir -p "$HOME" "$OLLAMA_MODELS"
    ollama serve &
    OLLAMA_PID=$!

    require_ollama() {
        if ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
            echo "ERROR: Managed Ollama exited; refusing HTTP readiness from another server"
            exit 1
        fi
    }
    timeout="${OLLAMA_START_TIMEOUT:-30}"
    [[ "$timeout" =~ ^[1-9][0-9]*$ ]] || { echo "ERROR: Invalid Ollama startup timeout"; exit 1; }
    deadline=$((SECONDS + timeout))
    while true; do
        require_ollama
        if curl --noproxy '*' --fail --silent --show-error --connect-timeout 1 --max-time 1 "$OLLAMA_HOST/api/version" > /dev/null 2>&1; then
            # Allow immediate bind failures to surface before trusting HTTP.
            sleep 0.1
            require_ollama
            echo "Managed Ollama ready at $OLLAMA_HOST"
            break
        fi
        require_ollama
        if ((SECONDS >= deadline)); then
            echo "ERROR: Managed Ollama readiness timeout after ${timeout}s at $OLLAMA_HOST"
            exit 1
        fi
        sleep 0.1
    done
    
    if [ "$LLM_PROVIDER" = "ollama" ]; then
        # Pull each distinct configured model exactly once. Exact tag matching
        # avoids treating another Gemma variant as the requested E4B artifact.
        declare -A REQUIRED_MODELS=()
        REQUIRED_MODELS["$SMART_MODEL"]=1
        REQUIRED_MODELS["$FAST_MODEL"]=1
        REQUIRED_MODELS["$ORCHESTRATOR_MODEL"]=1
        REQUIRED_MODELS["$DEEP_REASONING_MODEL"]=1
        for model in "${!REQUIRED_MODELS[@]}"; do
            echo "=========================================="
            echo "Checking for model: $model"
            echo "=========================================="
            if ! ollama list | awk 'NR > 1 {print $1}' | grep -Fxq "$model"; then
                echo "Model not found. Pulling $model..."
                ollama pull "$model"
            else
                echo "$model is available"
            fi
        done
    else
        echo "Cloud LLM provider selected; skipping local generation-model pulls"
    fi
fi

# Create necessary directories
mkdir -p "$DATA_DIR"/decisions
mkdir -p "$DATA_DIR"/logs
mkdir -p "$DATA_DIR"/chroma
mkdir -p "$DATA_DIR"/manuals

# Phase 6: Ensure agents.yaml exists in persistent config
if [ ! -f "${ADDON_CONFIG_DIR}/agents.yaml" ]; then
    echo "Creating default agents.yaml in /config..."
    if [ -f "${APP_DIR}/agents.yaml" ]; then
        cp "${APP_DIR}/agents.yaml" "${ADDON_CONFIG_DIR}/agents.yaml"
    else
        echo "agents: []" > "${ADDON_CONFIG_DIR}/agents.yaml"
    fi
fi

# Link "${ADDON_CONFIG_DIR}/agents.yaml" to where the app expects it (or update app to look in /config)
# For now, we update the app to use "${ADDON_CONFIG_DIR}/agents.yaml" via environment variable or symlink
rm -f "${APP_DIR}/backend/agents.yaml"
ln -sf "${ADDON_CONFIG_DIR}/agents.yaml" "${APP_DIR}/backend/agents.yaml"
echo "Linked agents.yaml to persistent storage"

echo "=========================================="
echo "Starting FastAPI Backend"
echo "=========================================="

# Start FastAPI backend
cd "$APP_DIR/backend"
"${BACKEND_EXECUTABLE:-python3}" -m uvicorn main:app \
    --host 0.0.0.0 \
    --port 8999 \
    --log-level "${LOG_LEVEL,,}" \
    --no-access-log &
API_PID=$!
while true; do
    if ! kill -0 "$API_PID" 2>/dev/null; then
        status=0
        wait "$API_PID" || status=$?
        echo "ERROR: FastAPI backend exited (status $status)"
        [ "$status" -ne 0 ] || status=1
        exit "$status"
    fi
    if [ -n "$OLLAMA_PID" ] && ! kill -0 "$OLLAMA_PID" 2>/dev/null; then
        echo "ERROR: Managed Ollama exited; stopping backend"
        exit 1
    fi
    sleep 0.2
done
