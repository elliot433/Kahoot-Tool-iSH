#!/bin/sh
# KahootKit installer for iSH (Alpine Linux)
echo "Installing KahootKit..."

apk add --no-cache python3 py3-pip nodejs 2>/dev/null
pip3 install requests websocket-client --break-system-packages 2>/dev/null || \
pip3 install requests websocket-client

echo ""
echo "Done! Run with: python3 kahoot.py"
