# Testing Setup

This document explains how to run tests with environment variables loaded from files.

## Quick Start

### Option 1: Using the test runner script (Recommended)
```bash
# Run all tests
./run_tests.sh

# Run with verbose output
./run_tests.sh -v

# Run specific test files
./run_tests.sh tests/auth/test_auth.py
./run_tests.sh tests/gtfs/test_trips_by_route.py

# Run tests matching a pattern
./run_tests.sh -k "gtfs"
./run_tests.sh -k "auth"
```

### Option 2: Manual environment loading
```bash
# Load environment and run tests
source tests/test.env
pytest -v
```

### Option 3: Direct pytest (environment auto-loaded)
```bash
# Environment variables are automatically loaded from conftest.py
pytest -v
```

## Environment Configuration

The test environment variables are defined in `tests/test.env`. This file contains:

- Database configuration for tests
- Test credentials for authentication
- Test data (route IDs, etc.)

### Environment File Priority

The system looks for environment files in this order:
1. `.env` (project root)
2. `tests/test.env` 
3. `.env.test` (project root)

The first file found will be used.

### Customizing Test Environment

To modify test settings, edit `tests/test.env`:

```bash
# Database configuration for tests
export ELETTRA_CONFIG_FILE=config/elettra-config.docker.yaml
export DATABASE_URL=postgresql+asyncpg://admin:admin@localhost:5440/elettra

# Test credentials
export TEST_LOGIN_EMAIL=test01.elettra@fart.ch
export TEST_LOGIN_PASSWORD=elettra

# Test data
export TEST_ROUTE_ID=5494f67e-7121-4628-b5b0-0c4fb21a236a
```

## Docker Compose Testing

### Start required services
```bash
docker-compose up -d db
```

### Run tests against docker services
```bash
./run_tests.sh -v
```

## Test Structure

- `tests/auth/` - Authentication and security tests
- `tests/gtfs/` - GTFS data management tests
- `tests/conftest.py` - Shared fixtures and environment loading
- `tests/test.env` - Test environment variables

## Reports

Test reports are automatically generated in `tests/reports/` with timestamps:
- Text reports: `{module}_{timestamp}.txt`
- JSON reports: `{module}_{timestamp}.json`

## Troubleshooting

### Environment not loading
- Check that `tests/test.env` exists and has correct format
- Ensure no spaces around `=` in environment file
- Verify file permissions

### Database connection issues
- Ensure `docker-compose up -d db` is running
- Check DATABASE_URL in test.env matches your setup
- Verify database is accessible on localhost:5440

### Import errors
- Ensure you're in the project root directory
- Check that `.venv` is activated
- Verify PYTHONPATH includes project root
