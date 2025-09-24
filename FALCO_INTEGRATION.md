# Falco Security Monitoring Integration

## Overview

Falco has been successfully integrated into your Elettra backend project to provide comprehensive runtime security monitoring for all containers.

## What's Been Added

### 1. Falco Security Monitoring
- **Container**: `elettra-falco`
- **Purpose**: Real-time security monitoring of all containers
- **Capabilities**: 
  - System call monitoring
  - Container escape detection
  - Privilege escalation detection
  - Network activity monitoring
  - File system access monitoring

### 2. Falco Metrics Exporter
- **Container**: `elettra-falco-exporter`
- **Purpose**: Prometheus metrics collection
- **Endpoint**: `http://localhost:9376/metrics`
- **Health Check**: `http://localhost:9377/health`

## Integration Approach

Falco has been **integrated directly into your main `docker-compose.yml`** file for simplicity. This means:

- ✅ **Single command deployment**: `docker-compose up -d`
- ✅ **Always active**: Security monitoring runs with your application
- ✅ **Unified management**: All services managed together
- ✅ **Simplified maintenance**: No separate compose files to manage

## Current Service URLs

| Service | URL | Purpose |
|---------|-----|---------|
| Backend API | http://localhost:8002 | Your FastAPI application |
| Frontend | http://localhost:55557 | React frontend |
| Database | localhost:5440 | PostgreSQL |
| MinIO Console | http://localhost:9003 | Object storage management |
| OSRM | http://localhost:5001 | Routing service |
| **Falco Metrics** | **http://localhost:9376/metrics** | **Security metrics** |

## Monitoring Coverage

Falco is actively monitoring:

### Database Security
- Unauthorized database connections
- Database file access attempts
- PostgreSQL-specific security events

### API Security
- Suspicious activity in API container
- Unauthorized file access
- Configuration file protection

### Storage Security
- MinIO data access monitoring
- Unauthorized storage access

### Network Security
- Unexpected network connections
- Container-to-container communication

### Container Security
- Container escape attempts
- Privilege escalation detection
- Sensitive file access

## Viewing Security Alerts

### Real-time Monitoring
```bash
# View live Falco alerts
docker-compose logs -f falco

# View all service logs
docker-compose logs -f
```

### Metrics and Health
```bash
# Check Falco metrics
curl http://localhost:9376/metrics

# Check Falco health
curl http://localhost:9377/health
```

## Custom Security Rules

Custom rules have been created in `falco/rules.d/elettra_rules.yaml` specifically for your application stack:

- **Database Rules**: Monitor PostgreSQL access
- **API Rules**: Protect FastAPI container
- **MinIO Rules**: Secure object storage
- **OSRM Rules**: Monitor routing service
- **Network Rules**: Detect unexpected network behavior

## Alert Levels

- **CRITICAL**: Container escape attempts, privilege escalation
- **WARNING**: Unauthorized access, suspicious activities  
- **INFO**: Normal operations and system events

## Example Security Events

You should see alerts like:
```
Notice Redirect stdout/stdin to network connection | connection=127.0.0.1:51736->127.0.0.1:9000
```

This indicates Falco is actively monitoring network connections (like MinIO health checks).

## Maintenance

### Updating Rules
1. Modify rules in `falco/rules.d/elettra_rules.yaml`
2. Restart Falco: `docker-compose restart falco`

### Log Management
```bash
# Clear Falco logs if they get too large
docker-compose exec falco sh -c "echo > /var/log/falco/falco.log"
```

### Performance Tuning
If you experience high CPU usage, you can adjust buffer settings in `falco/falco.yaml`.

## Security Benefits

With Falco integrated, you now have:

1. **Real-time threat detection** across all containers
2. **Compliance monitoring** for security best practices
3. **Audit trail** of all container activities
4. **Automated alerting** for suspicious behavior
5. **Metrics collection** for security dashboards

## Next Steps

1. **Monitor alerts**: Watch for security events in logs
2. **Set up alerting**: Configure webhooks for critical alerts
3. **Create dashboards**: Use Prometheus metrics for visualization
4. **Review rules**: Customize rules based on your specific needs
5. **Document incidents**: Track and respond to security events

## Troubleshooting

### Common Issues

**Falco not starting**: Check Docker socket permissions
```bash
ls -la /var/run/docker.sock
```

**No alerts**: Verify Falco is running
```bash
docker-compose ps falco
```

**High CPU usage**: Monitor system resources
```bash
docker stats elettra-falco
```

### Debug Mode
Enable debug logging by modifying the Falco command in `docker-compose.yml`:
```yaml
command: ["falco", "--log-level", "debug"]
```

## Resources

- [Falco Documentation](https://falco.org/docs/)
- [Falco Rules Reference](https://falco.org/docs/rules/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)

---

**Security monitoring is now active!** 🛡️

Your Elettra backend is now protected with comprehensive runtime security monitoring.
