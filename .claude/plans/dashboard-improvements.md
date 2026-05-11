# Dashboard Improvement Plan

## Phase 1: Critical UX Fixes + New Features (This Session)

### 1. Overview Page — Complete Redesign
- Add Alpheric-1 branding hero section with model status
- Add token usage time-series sparkline charts (in/out over time)
- Add request latency P50/P95/P99 display
- Fix "No data" empty states with proper icons + CTAs
- Add model usage breakdown (which models are being used most)
- Add "Quick Test" button to send test request from dashboard

### 2. New: Prompt Playground Page
- Interactive prompt testing with any available model
- Model selector dropdown
- Temperature, max_tokens, top_p sliders
- Send button with streaming response display
- Token count + latency + cost shown per response
- Side-by-side comparison mode (2 models)

### 3. Analytics Page — Enterprise Upgrade
- Token usage time-series chart (daily/hourly)
- Cost trend line chart
- Local vs External donut chart with drill-down
- Latency percentiles (P50/P95/P99) per model
- Top models by usage table
- Request volume heatmap by hour

### 4. Overview — Real-time Improvements
- Live request counter with animation
- Server status indicators (Ollama server 1, server 2, OpenClaw)
- Auto-refresh every 5 seconds
- Alpheric-1 model card with routing stats

### 5. All Pages — Empty State Improvements
- Consistent empty states with icon + descriptive text + CTA
- Follow Training page's gold-standard pattern

### 6. New Backend Endpoints
- GET /admin/analytics/token-timeseries — hourly token usage
- GET /admin/analytics/request-heatmap — request volume by hour
- POST /admin/playground — test prompt execution
