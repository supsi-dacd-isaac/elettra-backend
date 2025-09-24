# Falco Security Monitoring for Elettra Backend

This directory contains the Falco security monitoring configuration for the Elettra backend project.

## Overview

Falco is a runtime security tool that monitors system calls and container activities to detect suspicious behavior. This setup provides comprehensive security monitoring for your containerized Elettra application stack.

## Files

- `falco.yaml` - Main Falco configuration
- `rules.d/elettra_rules.yaml` - Custom security rules for Elettra
- `exporter-config.yaml` - Prometheus metrics exporter configuration
- `docker-compose.falco.yml` - Docker Compose configuration for Falco

## Quick Start

1. **Deploy Falco with your existing stack:**
   ```bash
   docker-compose -f docker-compose.yml -f docker-compose.falco.yml up -d
   ```

2. **Check Falco status:**
   ```bash
   docker-compose logs falco
   ```

3. **View security alerts:**
   ```bash
   docker-compose logs -f falco
   ```

## Monitoring Coverage

### Database Security
- Unauthorized database connections
- Database file access monitoring
- PostgreSQL-specific security rules

### API Security
- Suspicious activity in API container
- Unauthorized file access
- Configuration file protection

### Storage Security
- MinIO data access monitoring
- Unauthorized storage access

### Network Security
- Unexpected network connections
- Container-to-container communication monitoring

### Container Security
- Container escape detection
- Privilege escalation attempts
- Sensitive file access monitoring

## Custom Rules

The custom rules in `rules.d/elettra_rules.yaml` are specifically designed for your application stack:

- **Database Rules**: Monitor PostgreSQL access and configuration
- **API Rules**: Protect FastAPI container from suspicious activities
- **MinIO Rules**: Secure object storage access
- **OSRM Rules**: Monitor routing service integrity
- **Network Rules**: Detect unexpected network behavior

## Alert Levels

- **CRITICAL**: Container escape attempts, privilege escalation
- **WARNING**: Unauthorized access, suspicious activities
- **INFO**: Normal operations and system events

## Integration Options

### Prometheus Metrics
Falco exporter is included for Prometheus integration:
- Metrics endpoint: `http://localhost:9376/metrics`
- Health check: `http://localhost:9377/health`

### Log Integration
- JSON-formatted logs in `/var/log/falco/falco.log`
- Structured output for log aggregation systems

### Webhook Integration
Configure webhook output in `falco.yaml` for external alerting systems.

## Maintenance

### Updating Rules
1. Modify rules in `rules.d/elettra_rules.yaml`
2. Restart Falco: `docker-compose restart falco`

### Log Rotation
Falco logs are stored in a Docker volume. To manage log size:
```bash
docker-compose exec falco sh -c "echo > /var/log/falco/falco.log"
```

### Performance Tuning
Adjust buffer sizes and event rates in `falco.yaml` based on your system's performance requirements.

## Security Considerations

- Falco runs with privileged access to monitor system calls
- Ensure proper access controls on Falco configuration files
- Regularly review and update security rules
- Monitor Falco logs for any security events

## Troubleshooting

### Common Issues

1. **Falco not starting**: Check Docker socket permissions
2. **No alerts**: Verify rules are loaded correctly
3. **High CPU usage**: Adjust buffer settings in configuration

### Debug Mode
Enable debug logging by setting `FALCO_LOG_LEVEL=DEBUG` in the environment variables.

## Resources

- [Falco Documentation](https://falco.org/docs/)
- [Falco Rules Reference](https://falco.org/docs/rules/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
