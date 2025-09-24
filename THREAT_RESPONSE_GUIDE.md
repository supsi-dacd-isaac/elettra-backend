# Falco Threat Response Guide for Elettra Backend

## What Happens When a Threat is Detected?

When Falco detects a security threat in your Elettra backend, here's the complete response flow:

## 🚨 Immediate Response Actions

### 1. **Real-time Alerting**
```
Priority: CRITICAL
Rule: Container Escape Attempt
Time: 2025-09-24T15:11:42+0000
Container: elettra-api
User: root
Process: chroot
Command: chroot /host /bin/bash
```

### 2. **Multi-Channel Notifications**
- **Console Logs**: Visible in `docker-compose logs falco`
- **File Logs**: Saved to `/var/log/falco/falco.log`
- **JSON Output**: Structured for monitoring systems
- **Prometheus Metrics**: Available at `http://localhost:9376/metrics`

### 3. **Alert Levels & Responses**

| Priority | Response Actions | Examples |
|----------|------------------|----------|
| **CRITICAL** | 🚨 Immediate notification<br/>📧 Email alerts<br/>💬 Slack alerts<br/>🔍 Manual investigation | Container escape<br/>Privilege escalation<br/>Unauthorized DB access |
| **WARNING** | ⚠️ Standard alerting<br/>📝 Logging<br/>🔔 Notifications | Suspicious file access<br/>Unexpected network<br/>API anomalies |
| **INFO** | 📊 Logging only<br/>📈 Metrics collection | Normal operations<br/>System events |

## 🛡️ Enhanced Threat Response Options

### Option 1: Webhook Integration (Recommended)
```bash
# Start the webhook server
docker run -d --name falco-webhook \
  -p 8080:8080 \
  -v /var/log/falco-webhook:/var/log/falco-webhook \
  python:3.12-slim \
  python /app/webhook-server.py
```

**Benefits:**
- Custom alert processing
- Slack/Email integration
- Automated response scripts
- Alert storage and analysis

### Option 2: Direct Integration
```yaml
# In docker-compose.yml
falco:
  environment:
    - FALCO_HTTP_OUTPUT_ENABLED=true
    - FALCO_HTTP_OUTPUT_URL=http://webhook-server:8080/falco-webhook
```

### Option 3: External Monitoring
- **Grafana**: Visualize security metrics
- **ELK Stack**: Centralized logging
- **SIEM Systems**: Enterprise security monitoring

## 🔧 Configuring Threat Responses

### 1. **Enable Webhook Output**
```yaml
# falco/falco.yaml
http_output:
  enabled: true
  url: "http://localhost:8080/falco-webhook"
  user_agent: "elettra-falco-security"
```

### 2. **Configure Email Alerts**
```python
# falco/webhook-server.py
CONFIG = {
    'email_config': {
        'smtp_host': 'smtp.gmail.com',
        'smtp_port': 587,
        'username': 'your-email@gmail.com',
        'password': 'your-app-password',
        'from': 'falco-security@elettra.com',
        'to': ['admin@elettra.com', 'security@elettra.com']
    }
}
```

### 3. **Set Up Slack Integration**
```python
CONFIG = {
    'slack_webhook': 'https://hooks.slack.com/services/YOUR/SLACK/WEBHOOK'
}
```

## 🚀 Automated Response Actions

### Critical Threat Responses
```python
def execute_automated_response(alert_data):
    rule = alert_data.get('rule', '')
    
    if rule == 'Container Escape Attempt':
        # Log critical event
        logger.critical("CRITICAL: Container escape attempt detected")
        # Optionally: Isolate container, restart service
        
    elif rule == 'Privilege Escalation':
        # Log and alert
        logger.critical("CRITICAL: Privilege escalation detected")
        # Optionally: Block user, restart container
        
    elif rule == 'Unauthorized Database Access':
        # Log and block
        logger.critical("CRITICAL: Unauthorized database access")
        # Optionally: Block IP, restart database
```

## 📊 Monitoring & Analysis

### Real-time Monitoring
```bash
# Watch live security alerts
docker-compose logs -f falco

# Check specific threat types
docker-compose logs falco | grep "CRITICAL"

# Monitor metrics
curl http://localhost:9376/metrics | grep falco_events_total
```

### Alert Analysis
```bash
# Count alerts by priority
docker-compose logs falco | grep -c "CRITICAL"
docker-compose logs falco | grep -c "WARNING"

# Analyze specific threats
docker-compose logs falco | grep "Container Escape"
docker-compose logs falco | grep "Privilege Escalation"
```

## 🔍 Threat Investigation Workflow

### 1. **Immediate Response**
```bash
# Check current alerts
docker-compose logs falco --tail=20

# Identify affected containers
docker-compose ps

# Check container health
docker-compose exec elettra-api ps aux
```

### 2. **Forensic Analysis**
```bash
# Examine container logs
docker-compose logs elettra-api --tail=100

# Check file system changes
docker-compose exec elettra-api find /app -mtime -1

# Analyze network connections
docker-compose exec elettra-api netstat -tulpn
```

### 3. **Containment Actions**
```bash
# Restart affected service
docker-compose restart elettra-api

# Isolate container (if needed)
docker-compose stop elettra-api

# Check for persistence
docker-compose exec elettra-db psql -U admin -d elettra -c "SELECT * FROM pg_stat_activity;"
```

## 📈 Security Metrics Dashboard

### Key Metrics to Monitor
- **Alert Volume**: Number of alerts per hour/day
- **Threat Types**: Distribution of security events
- **Container Health**: Which containers trigger most alerts
- **Response Time**: Time from detection to response

### Sample Prometheus Queries
```promql
# Total security alerts
sum(rate(falco_events_total[5m])) by (priority)

# Critical alerts by container
sum(falco_events_total{priority="CRITICAL"}) by (container_name)

# Alert trends over time
increase(falco_events_total[1h])
```

## 🛠️ Customizing Threat Responses

### Adding Custom Rules
```yaml
# falco/rules.d/custom_rules.yaml
- rule: Custom Elettra Threat
  desc: Detect specific threat pattern
  condition: >
    container.name = "elettra-api" and
    proc.name = "suspicious_process"
  output: >
    Custom threat detected in Elettra API
    (container=%container.name process=%proc.name)
  priority: CRITICAL
  tags: [custom, elettra]
```

### Custom Response Scripts
```bash
#!/bin/bash
# /usr/local/bin/elettra-response.sh

RULE="$1"
CONTAINER="$2"
PRIORITY="$3"

case "$RULE" in
    "Container Escape Attempt")
        echo "CRITICAL: Isolating container $CONTAINER"
        docker stop "$CONTAINER"
        ;;
    "Privilege Escalation")
        echo "CRITICAL: Restarting service $CONTAINER"
        docker-compose restart "$CONTAINER"
        ;;
    *)
        echo "INFO: Logging threat $RULE"
        ;;
esac
```

## 🚨 Emergency Procedures

### Critical Threat Response
1. **Immediate**: Check `docker-compose logs falco` for latest alerts
2. **Assess**: Identify affected containers and services
3. **Contain**: Stop/restart affected services if necessary
4. **Investigate**: Analyze logs and system state
5. **Document**: Record incident details and response actions

### Contact Information
- **Security Team**: security@elettra.com
- **System Admin**: admin@elettra.com
- **Emergency**: +1-XXX-XXX-XXXX

## 📚 Resources

- [Falco Response Actions](https://falco.org/docs/alerts/)
- [Docker Security Best Practices](https://docs.docker.com/engine/security/)
- [Container Security Guide](https://kubernetes.io/docs/concepts/security/)

---

**Remember**: Falco provides detection and alerting. Your response actions determine the effectiveness of your security posture! 🛡️
