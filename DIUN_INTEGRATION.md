# Diun Docker Image Update Notifier for Elettra Backend

## Overview

Diun (Docker Image Update Notifier) has been integrated into your Elettra backend project to monitor Docker image updates and send notifications to Slack.

## What Diun Does

- **Monitors Docker Images**: Watches all containers in your Elettra backend stack
- **Detects Updates**: Checks for new image versions every 6 hours
- **Slack Notifications**: Sends alerts to your #settore-se-it-alerts channel
- **Database Tracking**: Maintains a database of image versions and update history

## Configuration

### Monitoring Schedule
- **Check Frequency**: Every 6 hours (`0 */6 * * *`)
- **First Check Notification**: Enabled (notifies on first run)
- **Workers**: 20 concurrent workers for efficient checking

### Monitored Containers
Diun automatically monitors all containers in your Elettra backend stack:
- `elettra-api` (FastAPI backend)
- `elettra-db` (PostgreSQL database)
- `elettra-falco` (Security monitoring)
- `elettra-falco-exporter` (Prometheus metrics)
- `elettra-osrm` (Routing service)
- `minio` (Object storage)
- `trip-shift-frontend` (React frontend)

### Slack Integration
- **Webhook**: Uses the same Slack webhook as Falco
- **Channel**: #settore-se-it-alerts
- **Format**: Rich messages with image details, tags, and update information

## Notification Format

When an image update is detected, you'll receive a Slack message like this:

```
🐳 Docker Image Update Alert

postgres:16-alpine has been updated!

📦 Image: postgres:16-alpine
🏷️ Tag: 16-alpine
📅 Date: 2025-01-15 10:30:00
🔗 Registry: docker.io
📊 Status: update

Container: elettra-db
Project: Elettra Backend

⚠️ Action Required: Please update your container to the latest version.
```

## Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Diun Database | `/data/diun.db` | Image version tracking |
| Docker Socket | `/var/run/docker.sock` | Container monitoring |

## Deployment

Diun is automatically deployed with your stack:

```bash
# Deploy with Diun
docker-compose up -d

# Check Diun status
docker-compose ps diun

# View Diun logs
docker-compose logs diun
```

## Monitoring and Maintenance

### Check Diun Status
```bash
# View Diun logs
docker-compose logs diun

# Check if Diun is running
docker-compose ps diun

# View recent activity
docker-compose logs diun --tail=20
```

### Manual Image Check
```bash
# Trigger manual check
docker-compose exec diun diun --config /etc/diun/diun.yml

# Check specific image
docker-compose exec diun diun --config /etc/diun/diun.yml --image postgres:16-alpine
```

### Database Management
```bash
# Access Diun database
docker-compose exec diun sqlite3 /data/diun.db

# View tracked images
docker-compose exec diun sqlite3 /data/diun.db "SELECT * FROM diun_entry;"
```

## Configuration Files

- **Main Config**: `diun/diun.yml`
- **Docker Compose**: `docker-compose.yml` (Diun service)
- **Environment**: Uses `SLACK_WEBHOOK_URL` from your `.env` file

## Customization

### Change Check Frequency
Edit `diun/diun.yml`:
```yaml
watch:
  schedule: "0 */12 * * *"  # Check every 12 hours instead of 6
```

### Add Custom Filters
```yaml
providers:
  docker:
    - name: "elettra-backend"
      filters:
        - name: "label"
          values: ["com.docker.compose.project=elettra-backend"]
        - name: "image"
          values: ["postgres:*", "redis:*"]  # Only monitor specific images
```

### Customize Slack Messages
Edit the `templateBody` in `diun/diun.yml`:
```yaml
notif:
  slack:
    templateBody: |
      🚀 **Image Update Detected!**
      
      **{{ .Entry.Image }}** has been updated!
      
      Please update your container: {{ .Entry.Metadata.ContainerName }}
```

## Benefits

1. **Security**: Stay updated with latest security patches
2. **Stability**: Get notified of important updates
3. **Automation**: No manual checking required
4. **Integration**: Uses existing Slack webhook
5. **Tracking**: Maintains history of image updates

## Troubleshooting

### Common Issues

**Diun not detecting updates**:
```bash
# Check Docker socket permissions
ls -la /var/run/docker.sock

# Verify Diun can access containers
docker-compose exec diun docker ps
```

**No Slack notifications**:
```bash
# Check webhook URL
echo $SLACK_WEBHOOK_URL

# Test webhook manually
curl -X POST "$SLACK_WEBHOOK_URL" -H "Content-Type: application/json" -d '{"text":"Test from Diun"}'
```

**Database issues**:
```bash
# Check database file permissions
docker-compose exec diun ls -la /data/

# Reset database (loses history)
docker-compose exec diun rm /data/diun.db
```

## Resources

- [Diun Documentation](https://crazymax.dev/diun/)
- [Diun GitHub Repository](https://github.com/crazy-max/diun)
- [Slack Integration Guide](https://crazymax.dev/diun/notif/slack/)

---

**Your Elettra backend now has automated Docker image update monitoring! 🐳**

Diun will keep you informed about all image updates in your stack via Slack notifications.
