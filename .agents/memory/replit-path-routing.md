---
name: Replit path-based routing conflict
description: How Replit routes requests to artifacts and how to avoid /api conflicts
---

## Rule
Each artifact's `paths` in `artifact.toml` is intercepted by the Replit proxy **before** any Vite dev server proxy rules are applied. If `artifacts/api-server` claims `paths = ["/api"]`, ALL `/api/*` browser requests go to the Node.js server on port 8080, bypassing any Vite proxy config entirely.

**Why:** Replit's path-based router routes requests to artifact services by path prefix. Vite's `proxy` config only works for requests that reach the Vite server — they never arrive if another artifact claims that prefix first.

**How to apply:** When adding a Python (or other non-Node) backend alongside an existing api-server artifact:
1. Check the api-server's `artifact.toml` for `paths` — if it claims `/api`, rename it (e.g., `/node-api`) using `verifyAndReplaceArtifactToml`.
2. Restart the api-server workflow so the new routing takes effect.
3. The Vite dev server's `proxy` config can then intercept `/api` requests and forward to the Python server at `localhost:8000`.
4. The WebSocket at `/ws` is not claimed by any artifact, so it routes to the root artifact (trading-dashboard) and Vite proxies it correctly.

**Fix applied:** api-server `artifact.toml` changed from `paths = ["/api"]` to `paths = ["/node-api"]`.
