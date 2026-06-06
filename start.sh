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
VENV="$SCRIPT_DIR/tina-agent"
PORT=8501

# Set this to the name you used in: cloudflared tunnel create <name>
# Leave empty to use an ephemeral Quick Tunnel instead.
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

# --- Start Streamlit ---
echo "Starting Streamlit on port $PORT..."
streamlit run "$SCRIPT_DIR/app.py" \
    --server.port "$PORT" \
    --server.headless true \
    --server.enableXsrfProtection true \
    &
STREAMLIT_PID=$!
echo "Streamlit PID: $STREAMLIT_PID"

sleep 4

# --- Start Cloudflare Tunnel ---
echo ""

# Check whether a named tunnel credentials file exists
CRED_FILE="$HOME/.cloudflared/${TUNNEL_NAME}.json"
# cloudflared also accepts credentials in the default dir by UUID; check by listing
CONFIG_FILE="$HOME/.cloudflared/config.yml"
if [[ -n "$TUNNEL_NAME" ]] && cloudflared tunnel list 2>/dev/null | grep -q "$TUNNEL_NAME"; then
    echo "Starting named Cloudflare Tunnel: $TUNNEL_NAME"
    echo "Your app will be available at: https://app.joseai.uk"
    echo ""
    if [[ -f "$CONFIG_FILE" ]]; then
        cloudflared tunnel --config "$CONFIG_FILE" run "$TUNNEL_NAME" 2>&1 &
    else
        cloudflared tunnel run "$TUNNEL_NAME" 2>&1 &
    fi
    TUNNEL_PID=$!
else
    echo "No named tunnel '$TUNNEL_NAME' found -- using Quick Tunnel (ephemeral URL)."
    echo "To get a permanent URL, run the one-time setup:"
    echo "  cloudflared tunnel login"
    echo "  cloudflared tunnel create ai-job-seeker"
    echo "  cloudflared tunnel route dns ai-job-seeker app.joseai.uk"
    echo ""
    echo "(look for the https://...trycloudflare.com URL below)"
    echo ""
    cloudflared tunnel --url "http://localhost:$PORT" 2>&1 &
    TUNNEL_PID=$!
fi

# --- Cleanup on Ctrl-C / SIGTERM ---
cleanup() {
    echo ""
    echo "Shutting down..."
    kill "$STREAMLIT_PID" "$TUNNEL_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

echo ""
echo "Press Ctrl+C to stop."
wait
