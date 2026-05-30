# Deploying the Pezzrr dashboard to snowflakepower.com

This host already serves `rongxinyin.com` and `flextech-energy.com` from the
**host nginx** (ports 80/443) with **certbot**-managed TLS. This deploy adds one
more nginx server block alongside them and runs the API as a **systemd** service
on `127.0.0.1:8077`. Nothing about the existing sites changes.

Components:

- **Dashboard** — static SPA built with `VITE_API_BASE=/api/v1`, served by nginx
  from `/var/www/snowflakepower.com/dist`.
- **API** — uvicorn (from the repo venv) as a systemd unit on `127.0.0.1:8077`,
  reverse-proxied at `/api/` by nginx.
- **TLS** — certbot `--nginx`, same as the other domains.

All steps below that touch `/etc`, `/var/www`, systemd, or certbot need `sudo`.
`$REPO` is `/home/ryin/github/pezzrr-app`.

---

## 0. Prerequisite — DNS (do this first)

certbot's HTTP-01 challenge fails until the domain resolves to this server
(`67.174.216.81`). At your DNS registrar:

- `snowflakepower.com`  A  →  `67.174.216.81`
- `www.snowflakepower.com`  A → `67.174.216.81`  *(optional; only if you want www)*

Verify before continuing (should print `67.174.216.81`):

```bash
dig +short snowflakepower.com A
```

> If you are **not** serving `www`, drop `-d www.snowflakepower.com` from the
> certbot command in step 4 and remove `www.snowflakepower.com` from
> `server_name` in the nginx config.

---

## 1. Build the dashboard

Already built into `$REPO/dashboard/dist` with the production API base. To
rebuild:

```bash
cd /home/ryin/github/pezzrr-app/dashboard
npm ci
VITE_API_BASE=/api/v1 npm run build
```

`VITE_API_BASE=/api/v1` makes the SPA call the API same-origin (`/api/v1/...`),
which nginx proxies to the backend — no CORS needed in production.

## 2. Publish the static files

```bash
sudo mkdir -p /var/www/snowflakepower.com
sudo cp -r /home/ryin/github/pezzrr-app/dashboard/dist /var/www/snowflakepower.com/dist
sudo chown -R www-data:www-data /var/www/snowflakepower.com
```

(Re-run the `cp` after each rebuild to publish updates.)

## 3. Install and start the API service

Make sure nothing else already holds port 8077 (e.g. a leftover dev uvicorn):

```bash
ss -tlnp | grep ':8077' || echo "8077 free"
# if a dev process is shown: pkill -f 'uvicorn api.main:app'
```

```bash
sudo cp /home/ryin/github/pezzrr-app/deploy/pezzrr-api.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now pezzrr-api.service
sudo systemctl status pezzrr-api.service --no-pager
# Confirm it is listening locally:
curl -s http://127.0.0.1:8077/api/v1/health   # -> {"status":"ok"}
```

> The unit runs `uvicorn ... --workers 1`. Keep a single worker: the control_bus
> MQTT result listener must run in one process. The API reads DB creds from
> `config/data_analytics_config.json` and JWT/MQTT/CORS from
> `config/api_config.json` (both already on the host, kept out of git).

## 4. Install the nginx site + issue TLS

```bash
# HTTP-only baseline block:
sudo cp /home/ryin/github/pezzrr-app/deploy/nginx/snowflakepower.com.conf \
        /etc/nginx/sites-available/snowflakepower.com
sudo ln -s /etc/nginx/sites-available/snowflakepower.com \
           /etc/nginx/sites-enabled/snowflakepower.com

# Validate config WITHOUT disturbing the other sites, then reload:
sudo nginx -t
sudo systemctl reload nginx

# Issue/install the certificate (certbot rewrites the block to add the TLS
# listeners and the 80->443 redirect, exactly like the other domains):
sudo certbot --nginx -d snowflakepower.com -d www.snowflakepower.com

# certbot reloads nginx itself; re-validate to be safe:
sudo nginx -t
```

`sudo nginx -t` is the safety gate — if it reports an error, the existing sites
keep running on the previously-loaded config; fix the new block and re-test
before reloading.

## 5. Verify

```bash
curl -sI  https://snowflakepower.com/                      # 200, serves the SPA
curl -s   https://snowflakepower.com/api/v1/health         # {"status":"ok"}
curl -sI  http://snowflakepower.com/                       # 301 -> https
```

Then in a browser: log in, open a home detail page, and confirm the live tiles
update (this exercises the SSE stream through the `/api/v1/stream/` proxy
location). Check the two existing sites still load.

---

## Operations

- **Logs:** `sudo journalctl -u pezzrr-api.service -f`
- **Restart API:** `sudo systemctl restart pezzrr-api.service`
- **Deploy new build:** rebuild (step 1) → re-copy dist (step 2) → hard-refresh.
  API code changes also need `sudo systemctl restart pezzrr-api.service`.
- **TLS renewal:** certbot installs a renewal timer automatically; no action
  needed. Check with `sudo certbot renew --dry-run`.

## Rollback

```bash
sudo rm /etc/nginx/sites-enabled/snowflakepower.com
sudo nginx -t && sudo systemctl reload nginx
sudo systemctl disable --now pezzrr-api.service
```

This removes the new site and stops the API; the existing sites are unaffected
because they were never modified.
