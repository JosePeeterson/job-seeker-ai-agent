#!/usr/bin/env bash
# start.sh -- Launch AI Job Seeker locally and expose it via Cloudflare Tunnel.
#
# Supports two modes (auto-detected):
#
#   NAMED TUNNEL (permanent URL) -- preferred
#     Requires a one-time setup:
#       cloudflared tunnel login
#       cloudflared tunnel create ai-job-seeker
#       cloudflared tunnel route dns ai-job-seeker app.joseai.uk
#     Then set TUNNEL_NAME below (or export it before running this script).
#
#   QUICK TUNNEL (ephemeral URL) -- fallback
#     No account needed. URL changes every restart.
#     Used automatically when no named tunnel is configured.
#
# Password setup:
#   python3 -c "import hashlib; print(hashlib.sha256(b'YOUR_PASSWORD').hexdigest())"
#   Add result to .streamlit/secrets.toml:  APP_PASSWORD_HASH = "<hash>"

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv"
PORT=8501
TUNNEL_PROTOCOL="${TUNNEL_PROTOCOL:-http2}"
TUNNEL_HOSTNAME="${TUNNEL_HOSTNAME:-app.joseai.uk}"
DEFAULT_OLLAMA_MODEL="${DEFAULT_OLLAMA_MODEL:-llama3.1:8b}"
PRELOAD_OLLAMA_MODEL="${PRELOAD_OLLAMA_MODEL:-1}"

# Set this to the name you used in: cloudflared tunnel create <name>
# This script now requires a named tunnel and will not use Quick Tunnel fallback.
TUNNEL_NAME="${TUNNEL_NAME:-ai-job-seeker}"

# --- Preflight checks ---
if ! command -v cloudflared &>/dev/null; then
    echo "ERROR: cloudflared not found."
    echo "Install it with:  brew install cloudflared"
    exit 1
fi

if [[ ! -f "$VENV/bin/activate" ]]; then
    echo "ERROR: Virtual environment not found at $VENV"
    exit 1
fi

# --- Password reminder ---
if [[ -z "${APP_PASSWORD_HASH:-}" ]]; then
    HAS_SECRETS=false
    if [[ -f "$SCRIPT_DIR/.streamlit/secrets.toml" ]]; then
        if grep -q 'APP_PASSWORD_HASH' "$SCRIPT_DIR/.streamlit/secrets.toml" 2>/dev/null; then
            HAS_SECRETS=true
        fi
    fi
    if [[ "$HAS_SECRETS" == "false" ]]; then
        echo "WARNING: No APP_PASSWORD_HASH set -- the app will be open to anyone."
        echo "Run:  python3 -c \"import hashlib; print(hashlib.sha256(b'YOUR_PASSWORD').hexdigest())\""
        echo "Then paste the hash into .streamlit/secrets.toml as APP_PASSWORD_HASH = \"<hash>\""
        echo ""
    fi
fi

# --- Activate virtual environment ---
source "$VENV/bin/activate"

# --- Ensure Ollama + default model are ready ---
if command -v ollama &>/dev/null; then
    echo "Checking Ollama service..."
    if ! ollama list >/dev/null 2>&1; then
        echo "Starting Ollama service in background..."
        nohup ollama serve >/tmp/ollama.log 2>&1 &
        for _ in {1..15}; do
            if ollama list >/dev/null 2>&1; then
                break
            fi
            sleep 1
        done
    fi

    if [[ "$PRELOAD_OLLAMA_MODEL" == "1" ]]; then
        if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$DEFAULT_OLLAMA_MODEL"; then
            echo "Default model already installed: $DEFAULT_OLLAMA_MODEL"
        else
            echo "Pulling default model: $DEFAULT_OLLAMA_MODEL"
            ollama pull "$DEFAULT_OLLAMA_MODEL"
        fi
    fi
else
    echo "WARNING: ollama command not found. Local Llama model preloading skipped."
fi

# --- Start Streamlit ---
echo "Starting Streamlit on port $PORT..."
export DEFAULT_OLLAMA_MODEL
streamlit run "$SCRIPT_DIR/app.py" \
    --server.port "$PORT" \
    --server.headless true \
    --server.enableXsrfProtection true \
    &
STREAMLIT_PID=$!
echo "Streamlit PID: $STREAMLIT_PID"

# Wait for Streamlit to become reachable before opening the tunnel.
for _ in {1..30}; do
    if command -v curl &>/dev/null && curl -fsS "http://localhost:$PORT" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

# --- Start Cloudflare Tunnel ---
echo ""

CONFIG_FILE="$HOME/.cloudflared/config.yml"
if ! cloudflared tunnel list >/dev/null 2>&1; then
    echo "ERROR: cloudflared is not authenticated for named tunnels on this machine."
    echo "Run the one-time setup:"
    echo "  cloudflared tunnel login"
    echo "  cloudflared tunnel create $TUNNEL_NAME"
    echo "  cloudflared tunnel route dns $TUNNEL_NAME $TUNNEL_HOSTNAME"
    echo ""
    echo "Then create $CONFIG_FILE with:"
    echo "  tunnel: <TUNNEL-UUID>"
    echo "  credentials-file: ~/.cloudflared/<TUNNEL-UUID>.json"
    echo "  ingress:"
    echo "    - hostname: $TUNNEL_HOSTNAME"
    echo "      service: http://localhost:$PORT"
    echo "    - service: http_status:404"
    exit 1
fi

if ! cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
    echo "ERROR: Named tunnel '$TUNNEL_NAME' was not found."
    echo "Create and route it with:"
    echo "  cloudflared tunnel create $TUNNEL_NAME"
    echo "  cloudflared tunnel route dns $TUNNEL_NAME $TUNNEL_HOSTNAME"
    exit 1
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: Missing Cloudflare config at $CONFIG_FILE"
    echo "Create it with:"
    echo "  tunnel: <TUNNEL-UUID>"
    echo "  credentials-file: ~/.cloudflared/<TUNNEL-UUID>.json"
    echo "  ingress:"
    echo "    - hostname: $TUNNEL_HOSTNAME"
    echo "      service: http://localhost:$PORT"
    echo "    - service: http_status:404"
    exit 1
fi

echo "Starting named Cloudflare Tunnel: $TUNNEL_NAME"
echo "Your app will be available at: https://$TUNNEL_HOSTNAME"
echo "Tunnel protocol: $TUNNEL_PROTOCOL"
echo ""
cloudflared tunnel --protocol "$TUNNEL_PROTOCOL" --config "$CONFIG_FILE" run "$TUNNEL_NAME" 2>&1 &
TUNNEL_PID=$!

# --- Cleanup on Ctrl-C, SIGTERM, or Terminal Close ---
cleanup() {
    echo ""
    echo "Shutting down background services..."
    # Kill the specific PIDs managed by this instance
    kill "$STREAMLIT_PID" "$TUNNEL_PID" 2>/dev/null || true
    
    # Optional: Safeguard to ensure no orphaned ollama background tasks hang around
    # pkill -f "ollama serve" 2>/dev/null || true
    
    exit 0
}
# Added HUP and EXIT to catch terminal closures and abrupt script endings
trap cleanup INT TERM HUP EXIT

echo ""
echo "Press Ctrl+C to stop."
wait