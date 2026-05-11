#!/bin/bash
set -e

echo "🚀 Starting Friday AI — Production Mode"
echo "   Bot: python main.py"
echo "   API: node artifacts/api-server/dist/index.mjs"

# Build api-server if dist doesn't exist
if [ ! -f "artifacts/api-server/dist/index.mjs" ]; then
    echo "📦 Building API server..."
    pnpm --filter @workspace/api-server run build
fi

# Start Python bot (Flask on PORT 5000, polling in background)
export PORT=5000
python main.py &
BOT_PID=$!
echo "✅ Bot started (PID $BOT_PID) on port 5000"

# Give bot time to initialize
sleep 4

# Check bot is still running
if ! kill -0 $BOT_PID 2>/dev/null; then
    echo "❌ Bot failed to start — check logs"
    exit 1
fi

# Start Node.js API server in foreground (port 8080 — health probe target)
export PORT=8080
export NODE_ENV=production
echo "✅ Starting API server on port 8080..."
exec node --enable-source-maps artifacts/api-server/dist/index.mjs
