#!/bin/bash
# Warm up Ollama models on both servers to avoid cold-start latency
echo "Warming up Ollama models..."

# Server 1: 10.0.0.9
echo "  Server 1 (10.0.0.9)..."
curl -s http://10.0.0.9:11434/api/generate -d '{"model":"llama3.2:latest","prompt":"hi","options":{"num_predict":1}}' > /dev/null 2>&1 &
curl -s http://10.0.0.9:11434/api/generate -d '{"model":"deepseek-coder:6.7b","prompt":"hi","options":{"num_predict":1}}' > /dev/null 2>&1 &
curl -s http://10.0.0.9:11434/api/generate -d '{"model":"deepseek-coder-v2:16b","prompt":"hi","options":{"num_predict":1}}' > /dev/null 2>&1 &

# Server 2: 10.0.0.10
echo "  Server 2 (10.0.0.10)..."
curl -s http://10.0.0.10:11434/api/generate -d '{"model":"codellama:13b","prompt":"hi","options":{"num_predict":1}}' > /dev/null 2>&1 &
curl -s http://10.0.0.10:11434/api/generate -d '{"model":"deepseek-r1:8b","prompt":"hi","options":{"num_predict":1}}' > /dev/null 2>&1 &
curl -s http://10.0.0.10:11434/api/generate -d '{"model":"mistral:7b","prompt":"hi","options":{"num_predict":1}}' > /dev/null 2>&1 &

# Wait for all warm-ups
wait
echo "  All models warmed up!"
