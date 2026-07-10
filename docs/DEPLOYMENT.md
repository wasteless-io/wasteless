# Deployment Guide

> Deploying Wasteless outside your laptop: VPS setup, automation, backups, hardening.

---

## Deployment options

| Option | When to use | Cost | Complexity |
|--------|-------------|------|------------|
| **Local** | Development, evaluation | $0 | Low |
| **VPS** | Demos, small teams, 1–5 accounts | €5–30/mo | Low |
| **Client AWS account** | Compliance-sensitive clients | client pays | Medium (planned) |

The supported production path today is a **single VPS running Docker Compose
(PostgreSQL) + the FastAPI UI + the bundled scheduler (`wasteless schedule`)**.
Terraform-based deployment into a client AWS account is on the roadmap;
`terraform/` currently only contains detector test fixtures.

---

## VPS deployment

**Time**: ~1 hour. Any provider works; Hetzner CX21-class (2 vCPU, 4 GB) is
plenty.

### 1. Provision and secure the server

```bash
ssh root@YOUR_VPS_IP

apt update && apt upgrade -y
apt install -y curl git ufw fail2ban unattended-upgrades

# Dedicated user
useradd -m -s /bin/bash wasteless
usermod -aG sudo wasteless
mkdir -p /home/wasteless/.ssh
cp /root/.ssh/authorized_keys /home/wasteless/.ssh/
chown -R wasteless:wasteless /home/wasteless/.ssh
chmod 700 /home/wasteless/.ssh && chmod 600 /home/wasteless/.ssh/authorized_keys

# Firewall — do NOT expose 5432 or 8888 directly
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw enable
```

SSH hardening (`/etc/ssh/sshd_config`): `PermitRootLogin no`,
`PasswordAuthentication no`, `AllowUsers wasteless`, then
`systemctl restart sshd`. Enable fail2ban with the default sshd jail.

### 2. Install Docker

```bash
curl -fsSL https://get.docker.com | sh
usermod -aG docker wasteless
apt install -y docker-compose-plugin
systemctl enable docker
```

### 3. Install Wasteless

```bash
su - wasteless
git clone https://github.com/wasteless-io/wasteless.git
cd wasteless

cp .env.template .env
nano .env
```

Production `.env`:

```bash
AWS_REGION=eu-west-1
AWS_ACCOUNT_ID=123456789012
# Roles created by the onboarding stack/module (see AWS_SETUP.md)
AWS_ROLE_ARN=arn:aws:iam::123456789012:role/wasteless-readonly
AWS_WRITE_ROLE_ARN=arn:aws:iam::123456789012:role/wasteless-remediation
# Source credentials that assume the roles: ~/.aws, instance profile,
# or legacy static keys (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY)

DB_HOST=localhost
DB_PORT=5432
DB_NAME=wasteless
DB_USER=wasteless
DB_PASSWORD=STRONG_PASSWORD_HERE   # change this!

LOG_LEVEL=INFO
```

Then:

```bash
# Backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Database
docker compose up -d postgres

# UI (own venv, mirror the DB credentials in ui/.env)
cd ui && ./install.sh && ./start.sh
```

The UI listens on `127.0.0.1:8888` by default (`WASTELESS_HOST` overrides
the bind address). Keep it on loopback: the API has no authentication and
its POST endpoints execute real AWS actions — only expose it through the
authenticated reverse proxy below, never by binding `0.0.0.0` directly.

### 4. Reverse proxy + TLS + auth

Put the UI behind a reverse proxy **with authentication**: the UI has no
built-in auth yet, and its POST endpoints execute real AWS actions. Example
with Caddy (auto-TLS via Let's Encrypt, basic auth included):

```bash
apt install -y caddy
caddy hash-password    # prompts for a password, prints the bcrypt hash
```

`/etc/caddy/Caddyfile`:

```
wasteless.yourdomain.com {
    basic_auth {
        # paste the hash printed by `caddy hash-password`
        admin $2a$14$REPLACE_WITH_HASH
    }
    reverse_proxy localhost:8888
}
```

```bash
systemctl reload caddy
```

Point a DNS A record at the VPS IP. Nginx + certbot works equally well
(`auth_basic` + `htpasswd`). An IP allowlist or a VPN (Tailscale,
WireGuard) instead of — or on top of — basic auth is even better. Never
deploy the proxy without one of these.

### 5. Automation

Collection and detection are scheduled by the bundled CLI — no hand-written
crontab entries:

```bash
./wasteless.sh schedule    # OS-level schedule, every 5 min, survives reboot
./wasteless.sh status      # UI + schedule state
```

`wasteless schedule` picks the right backend for the host (launchd on
macOS, a systemd user timer on Linux — with `loginctl enable-linger` so it
runs without an open SSH session — or crontab as fallback) and runs the
full pipeline (`wasteless collect`: CloudWatch collection + all detectors)
every 5 minutes. Output goes to `~/.wasteless.log`; partial runs (e.g.
Steampipe not installed) are recorded in `collection_runs` and flagged in
the UI. Remove with `./wasteless.sh unschedule`. See
[AUTOMATION_GUIDE.md](AUTOMATION_GUIDE.md).

Stale-recommendation cleanup needs no extra job: the UI's built-in
`sync_aws_job` marks recommendations `obsolete` every 5 minutes when the
underlying resource is gone (see [CLEANUP_GUIDE.md](CLEANUP_GUIDE.md)).

### 6. Backups

```bash
mkdir -p ~/wasteless/backups
cat > ~/wasteless/scripts/backup_db.sh << 'EOF'
#!/bin/bash
BACKUP_DIR="$HOME/wasteless/backups"
FILE="wasteless_backup_$(date +%Y%m%d_%H%M%S).sql"
docker exec wasteless-postgres pg_dump -U wasteless wasteless > "${BACKUP_DIR}/${FILE}"
gzip "${BACKUP_DIR}/${FILE}"
find "${BACKUP_DIR}" -name "*.sql.gz" -mtime +30 -delete
EOF
chmod +x ~/wasteless/scripts/backup_db.sh

crontab -e
# 0 1 * * * /home/wasteless/wasteless/scripts/backup_db.sh >> /home/wasteless/wasteless/logs/backup.log 2>&1
```

Optional off-site copy:

```cron
30 1 * * * aws s3 sync /home/wasteless/wasteless/backups/ s3://your-backup-bucket/
```

Restore:

```bash
gunzip -c backups/wasteless_backup_YYYYMMDD.sql.gz | \
  docker exec -i wasteless-postgres psql -U wasteless -d wasteless
```

**Test a restore at least once before you need it.**

---

## Production checklist

### Security
- [ ] HTTPS via reverse proxy, valid certificate
- [ ] UI not directly exposed (proxy auth / IP allowlist / VPN)
- [ ] Firewall: only 22/80/443 open; 5432 and 8888 bound to localhost
- [ ] SSH key-only, root login disabled, fail2ban active
- [ ] Strong DB password; `.env` not in Git
- [ ] IAM: read-only for detection; write actions added deliberately
      (see [AWS_SETUP.md](AWS_SETUP.md))
- [ ] `auto_remediation.enabled: false` until the dry-run period validated

### Reliability
- [ ] Daily DB backups + tested restore
- [ ] Auto-collection scheduled (`./wasteless.sh status` shows it active)
- [ ] Container restart policy (`restart: unless-stopped`)
- [ ] Uptime check on the UI (UptimeRobot or similar)
- [ ] Weekly look at `logs/` for ERROR lines

---

## Monitoring

Minimal health-check script, run every 15 min from cron:

```bash
#!/bin/bash
docker ps | grep -q wasteless-postgres || echo "ALERT: postgres container down"
curl -sf http://localhost:8888/ > /dev/null || echo "ALERT: UI not responding"
docker exec wasteless-postgres pg_isready -U wasteless > /dev/null 2>&1 \
  || echo "ALERT: postgres not accepting connections"
DISK=$(df -h / | awk 'NR==2 {print $5}' | tr -d '%')
[ "$DISK" -gt 80 ] && echo "ALERT: disk at ${DISK}%"
```

Pipe the output to mail/Slack as needed. For AWS API usage, keep an eye on the
CloudWatch bill (see cost table in [AUTOMATION_GUIDE.md](AUTOMATION_GUIDE.md)).

---

## Scaling

A single small VPS comfortably handles one AWS account. When it stops being
enough:

1. **Vertical**: bump the VPS tier (Postgres and the collectors are the only
   real consumers).
2. **Database**: move PostgreSQL to a managed instance (RDS) and point both
   `.env` files at it.
3. **Multi-account**: one clone + `.env` + schedule per account (see FAQ in
   [AUTOMATION_GUIDE.md](AUTOMATION_GUIDE.md)); native multi-account support
   is on the roadmap.

---

## Troubleshooting

| Symptom | Check |
|---------|-------|
| Containers won't start | `docker compose logs`, `df -h`, `free -h` |
| DB connection errors | `docker exec wasteless-postgres pg_isready -U wasteless`, credentials in both `.env` files |
| UI up but empty | Did collection/detection run? `tail ~/.wasteless.log`, run `./wasteless.sh collect` manually |
| Stale recommendations | Run cleanup: `python src/utils/cleanup_orphaned_recommendations.py --dry-run` |
| TLS not issued | DNS record propagated? `journalctl -u caddy` |
| High memory | `docker stats`; `VACUUM` the database if it has grown large |

---

## Related documentation

- [Architecture](ARCHITECTURE.md)
- [AWS Setup](AWS_SETUP.md)
- [Automation Guide](AUTOMATION_GUIDE.md)
- [Development](DEVELOPMENT.md)
- [Contributing](../CONTRIBUTING.md)
