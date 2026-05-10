#!/bin/bash
set -e

echo "🚀 Starting Friday AI Bot (Production)..."

# Start Python bot in background on port 5000
export PORT=5000
python main.py &
BOT_PID=$!
echo "✅ Bot started (PID $BOT_PID)"

# Wait for bot to be ready
sleep 3

# Start Node.js API server (foreground, port 8080)
export PORT=8080
exec node --enable-source-maps artifacts/api-server/dist/index.mjs
