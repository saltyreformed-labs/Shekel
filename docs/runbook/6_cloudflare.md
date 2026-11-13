# Shekel Operations Runbook: Cloudflare Management

Section 6 of the runbook, whose contents and quick reference are
[`docs/runbook.md`](../runbook.md).

## 6. Cloudflare Management

### 6.1 Tunnel Status

```bash
# Check the systemd service.
sudo systemctl status cloudflared

# View recent tunnel logs.
journalctl -u cloudflared --no-pager -n 30

# List all tunnels on the account.
cloudflared tunnel list

# Check tunnel connectivity (should show active connections).
cloudflared tunnel info shekel
```

### 6.2 Restarting the Tunnel

```bash
sudo systemctl restart cloudflared
```

**When to restart:**

- After editing `/etc/cloudflared/config.yml`
- After a cloudflared package update
- If the tunnel shows connection errors in `journalctl`

**Note:** Restarting cloudflared causes a brief (<5 second) interruption in external access.
Internal (LAN) access via `http://localhost` is not affected.

### 6.3 Tunnel Configuration Changes

```bash
# Edit the config file.
sudo nano /etc/cloudflared/config.yml

# Validate the config (dry run).
cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate

# Restart to apply changes.
sudo systemctl restart cloudflared

# Verify the tunnel is running.
sudo systemctl status cloudflared
curl -s https://<domain>/health
```

### 6.4 Adding a New Authorized User (Cloudflare Access)

Cloudflare Access controls who can reach the application. Only email addresses in the Access policy
can pass through.

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Navigate to **Zero Trust** (or `https://one.dash.cloudflare.com`)
3. Go to **Access** > **Applications**
4. Click on **Shekel Budget App**
5. Edit the **Allowed Users** policy
6. Under **Include** > **Emails**, add the new email address
7. Click **Save**

The new user can now authenticate via Cloudflare Access (email OTP or configured identity provider)
and reach the Shekel login page.

**Note:** This only grants access through the Cloudflare layer. The user still needs a Shekel
account (via seed script or future registration) to log into the application.

### 6.4a Attaching an Access Policy at the cloudflared Level (Commit C-37)

Sections 6.4 and 6.5 cover the dashboard side of Access management. This subsection covers the
matching `cloudflared/config.yml` ingress block that is required for the audit fix to take effect.
Audit finding F-061.

The committed `cloudflared/config.yml` carries an `originRequest.access` block:

```yaml
originRequest:
  noTLSVerify: true
  access:
    required: true
    teamName: <TEAM_NAME>
    audTag:
      - <AUD_TAG>
```

Before the first `cloudflared` start (or after rotating the Access application), replace the
placeholders:

1. **`<TEAM_NAME>`** -- the subdomain of `cloudflareaccess.com` for your Zero Trust team. Find it
   under **Zero Trust** > **Settings** > **Custom Pages** (top of the page) or in the URL of any
   Access application page (`https://<TEAM>.cloudflareaccess.com/...`).

2. **`<AUD_TAG>`** -- the Application Audience tag. Each Access application has its own AUD. Find it
   under:
   - **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
   - Click into the application; the **Overview** tab lists **Application Audience (AUD) Tag** as a
     64-character hex string.

3. Apply the placeholders on the host:

   ```bash
   sudo $EDITOR /etc/cloudflared/config.yml
   # Replace <TEAM_NAME> and <AUD_TAG> with the values from steps 1 and 2.

   # Validate the config syntax before reload.
   cloudflared tunnel --config /etc/cloudflared/config.yml ingress validate

   # Apply.
   sudo systemctl restart cloudflared
   ```

4. Verify the policy is enforced. From a browser without an active Access session:

   ```text
   https://<DOMAIN>/health
   ```

   The expected response is the Cloudflare Access login page. A direct `200 OK` with the JSON health
   payload would mean the policy is NOT applied; recheck the AUD tag and the ``required: true``
   flag.

**What `required: true` does.** cloudflared validates the `Cf-Access-Jwt-Assertion` header on every
request. Without a valid JWT for the AUD above, cloudflared returns 403 at the edge -- the request
never reaches Nginx. This closes the credential-stuffing surface on `/login`: even an attacker with
a leaked Shekel password cannot reach the login form without a valid Access JWT first.

**Operator emergency bypass.** If the Cloudflare Access dashboard is unreachable (rare; depends on
Cloudflare's own auth chain) and you need to log into Shekel, use the LAN bypass:

```bash
# From a machine on the LAN, reach Nginx directly without going
# through cloudflared.  This skips the Access check.
curl -sI http://<LAN_HOST>/health
```

Then connect to `http://<LAN_HOST>` in a browser; you are now past cloudflared and only Shekel's own
login + MFA stand between you and the app. Restore the Access posture as soon as the dashboard is
reachable again.

### 6.4b Cloudflared Metrics Endpoint Binding (Commit C-37)

The committed `cloudflared/config.yml` pins the metrics endpoint to loopback only. Audit finding
F-128.

```yaml
metrics: 127.0.0.1:2000
```

**Why this matters.** The default `cloudflared` behaviour binds the Prometheus metrics endpoint on
`0.0.0.0:2000` inside the container, making it reachable from every other peer on whatever Docker
bridge cloudflared is attached to. An attacker landing in any sibling container could poll
`/metrics` for tunnel health, request counts, and connection state -- operational data that should
not leak laterally even within the trusted homelab subnet.

Pinning to `127.0.0.1` keeps the endpoint reachable only from inside the cloudflared container
itself.

**Verifying the bind.** From inside the cloudflared container, the endpoint must answer; from any
sibling container, it must not.

```bash
# Inside cloudflared -- expect a 200 with metrics output.
docker exec cloudflared wget -qO- http://127.0.0.1:2000/metrics | head

# From the app container -- expect a connection refused.
# (This catches an accidental rebind to 0.0.0.0.)
docker exec shekel-prod-app sh -c \
    'wget -qO- --timeout=2 http://cloudflared:2000/metrics' \
    && echo "FAIL: metrics endpoint reachable from app" \
    || echo "OK: metrics endpoint not reachable from app"
```

The second probe should print `OK:`. If it prints `FAIL:`, the metrics directive in
`cloudflared/config.yml` is missing or has been overridden somewhere in the Cloudflare dashboard or
the systemd unit file -- check those before opening an incident.

### 6.5 Removing an Authorized User

1. Navigate to **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
2. Edit the **Allowed Users** policy
3. Remove the email address from the **Include** > **Emails** list
4. Click **Save**

The user's existing Cloudflare Access session will expire (default: 24 hours). To revoke access
immediately:

1. Navigate to **Zero Trust** > **Access** > **Applications** > **Shekel Budget App**
2. Click the **Overview** tab
3. Under **Active Sessions**, find and revoke the user's session

### 6.6 Updating WAF Rate Limit Rules

The Cloudflare WAF rate limits protect `/login` and `/auth/mfa/verify` against brute-force attacks
at the network edge (before traffic reaches the origin).

Current configuration:

| Rule | Path | Method | Rate | Block Duration |
|------|------|--------|------|---------------|
| Login brute force protection | `/login` | POST | 20 per 10 seconds | 60 seconds |
| MFA brute force protection | `/auth/mfa/verify` | POST | 20 per 10 seconds | 60 seconds |

To modify thresholds:

1. Log in to the Cloudflare dashboard: `https://dash.cloudflare.com`
2. Select your domain
3. Navigate to **Security** > **WAF** > **Rate limiting rules**
4. Click the rule name (e.g., `Login brute force protection`)
5. Click **Edit**
6. Adjust the **Rate** or **Period** fields
7. Click **Save**

Changes take effect within seconds.

### 6.7 Rotating Tunnel Credentials

If tunnel credentials are compromised:

```bash
# Delete the old tunnel.
cloudflared tunnel delete shekel

# Create a new tunnel (generates new credentials).
cloudflared tunnel create shekel

# Update the DNS record.
cloudflared tunnel route dns shekel <domain>

# Update /etc/cloudflared/config.yml with the new tunnel ID.
sudo nano /etc/cloudflared/config.yml
# Replace the tunnel UUID and credentials-file path.

# Restart cloudflared.
sudo systemctl restart cloudflared

# Verify.
curl -s https://<domain>/health
```
