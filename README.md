# Elettra Backend

A FastAPI-based backend service for analyzing electric bus pre-feasibility studies in Swiss public transport systems. The tool integrates with **pfaedle** for precise route shape generation from Swiss GTFS data.

## 🚌 Features

### Core Functionality
- **Energy Consumption Analysis**: Calculate energy requirements for different electric bus types
- **Battery Sizing Optimization**: Determine optimal battery capacity with Swiss market pricing
- **Real Route Analysis**: Use actual Swiss transport route data for accurate simulations
- **Company Data Management**: Multi-tenant architecture for transport companies

### Swiss Transport Integration
- **GTFS Data Processing**: Download and process Swiss public transport timetables
- **pfaedle Integration**: Generate precise route shapes from OpenStreetMap data
- **Multi-Operator Support**: SBB, PostBus, ZVV, and other Swiss operators
- **Real Terrain Analysis**: Elevation profiles and Swiss-specific terrain factors

### Technical Features
- **JWT Authentication**: Secure API access with token persistence
- **PostgreSQL Database**: Comprehensive data model with spatial support
- **Async FastAPI**: High-performance async endpoints
- **Flexible Configuration**: YAML-based configuration system
- **Auto-generated Documentation**: Interactive Swagger/OpenAPI docs

## 🛠️ Prerequisites

### System Requirements
- Python 3.8+
- PostgreSQL 12+ with PostGIS extension
- **pfaedle** binary installed on system
- At least 2GB RAM for GTFS processing
- 10GB+ storage for Swiss transport data

### Installing pfaedle

**Ubuntu/Debian:**
```bash
sudo apt-get update
sudo apt-get install build-essential cmake libzip-dev zlib1g-dev libbz2-dev
git clone --recurse-submodules https://github.com/ad-freiburg/pfaedle
cd pfaedle
mkdir build && cd build
cmake ..
make -j
sudo make install
```

**macOS:**
```bash
brew install pfaedle
```

### Database Setup
```sql
-- Create database
CREATE DATABASE elettra;

-- Enable extensions
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "postgis";

-- Run the schema files
\i database_extensions.sql
```

## 📦 Installation

1. **Clone the repository:**
```bash
git clone <repository-url>
cd elettra-backend
```

2. **Create virtual environment:**
```bash
python -m venv .venv
source .venv/bin/activate  # Linux/macOS
# .venv\Scripts\activate     # Windows
```

3. **Install dependencies:**
```bash
pip install -r requirements.txt
```

4. **Configure the application:**
```bash
# Set configuration file path
export ELETTRA_CONFIG_FILE="$(pwd)/config/elettra-config.yaml"

# Edit configuration
cp config/elettra-config.yaml.example config/elettra-config.yaml
# Update database credentials and other settings
```

5. **Run database migrations:**
```bash
# Apply the new database schema
psql -h localhost -U your_user -d elettra -f database_extensions.sql
```

## 🚀 Running the Application

```bash
# Development mode
python main.py

# Production mode with gunicorn
gunicorn main:app -w 4 -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8001
# Production mode with uvicorn
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

The API will be available at:
- **API**: http://localhost:8000
- **Interactive docs**: http://localhost:8000/docs
- **ReDoc**: http://localhost:8000/redoc

## 🗺️ Swiss Transport Data Integration

### Available Data Sources

The system supports multiple Swiss transport operators:

| Operator | Coverage | Data Type | Route Types |
|----------|----------|-----------|-------------|
| **SBB** | National | Complete timetable | Rail, Bus, Tram |
| **PostBus** | National | Bus routes | Bus |
| **ZVV** | Zurich region | Regional transport | Bus, Tram, Train |

### GTFS Processing Workflow

1. **Create GTFS Dataset**:
```bash
curl -X POST "http://localhost:8000/api/v1/routes/gtfs-datasets" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"dataset_name": "SBB Winter 2024"}'
```

2. **Process with pfaedle**:
```bash
curl -X POST "http://localhost:8000/api/v1/routes/gtfs-datasets/{dataset_id}/process-swiss?source_key=sbb" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

3. **Query processed routes**:
```bash
curl -X GET "http://localhost:8000/api/v1/routes/transit-routes?route_type=3" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Route Analysis Example

Get detailed electrification analysis for a specific route:
```bash
curl -X GET "http://localhost:8000/api/v1/algorithms/route-analysis/{route_id}" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

Returns:
- Route characteristics (distance, elevation, stops)
- Energy requirements for different bus types  
- Infrastructure recommendations
- Cost estimates in CHF

## 🔋 Energy Analysis Algorithms

### Basic Energy Calculation
```python
# Generic calculation
POST /api/v1/algorithms/energy-consumption
{
  "route_length_km": 25.5,
  "bus_type": "standard",
  "passenger_capacity": 80,
  "average_speed_kmh": 30.0,
  "terrain_factor": 1.2,
  "climate_factor": 1.1
}
```

### Route-Based Analysis
```python
# Using real Swiss route data
POST /api/v1/algorithms/energy-consumption/route/{route_id}
{
  "bus_type": "standard",
  "passenger_capacity": 80,
  "climate_factor": 1.1
}
```

### Battery Sizing
```python
POST /api/v1/algorithms/battery-sizing
{
  "daily_energy_requirement_kwh": 250.0,
  "safety_margin": 0.25,
  "degradation_factor": 0.85,
  "charging_efficiency": 0.92
}
```

## 🏗️ API Architecture

### Authentication Flow
1. **Login**: `POST /api/v1/auth/login`
2. **Use Token**: Add `Authorization: Bearer <token>` header
3. **Token Persistence**: Automatically saved in Swagger UI

### Main Endpoints

| Endpoint Group | Description | Key Features |
|---------------|-------------|--------------|
| `/auth` | Authentication | JWT tokens, user management |
| `/data` | Company Data | Bus models, fleet inventory |
| `/routes` | Swiss Transport | GTFS datasets, transit routes |
| `/algorithms` | Analysis | Energy calculations, battery sizing |
| `/simulations` | Simulations | Flexible simulation parameters |

### Database Schema

```
companies
├── users
├── bus_models
├── fleet_inventory
├── gtfs_datasets
│   └── transit_routes
│       ├── route_stops
│       └── route_shapes (enhanced by pfaedle)
└── simulation_runs
```

## ⚙️ Configuration

Configuration is managed via YAML files:

```yaml
# config/elettra-config.yaml
app:
  name: "Elettra Backend"
  version: "2.0.0"
  debug: true

database:
  host: "localhost"
  port: 5432
  username: "elettra_user"
  password: "secure_password"
  database: "elettra"
  echo: false

server:
  host: "0.0.0.0"
  port: 8000
  reload: true

security:
  secret_key: "your-secret-key-here"
  algorithm: "HS256"
  access_token_expire_minutes: 1440

cors:
  origins: ["http://localhost:3000", "http://localhost:8080"]

# pfaedle processing settings
gtfs:
  work_directory: "/tmp/elettra_gtfs"
  max_file_size_mb: 500
  processing_timeout_minutes: 60
```

## 🧪 Testing Swiss Data Integration

### Test pfaedle Installation
```bash
pfaedle --help
# Should show pfaedle usage information
```

### Download Test Data
```bash
# Small test dataset - Zurich ZVV
curl -X POST "http://localhost:8000/api/v1/routes/gtfs-datasets" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{"dataset_name": "ZVV Test"}'

# Process with Zurich data (smaller dataset)
curl -X POST "http://localhost:8000/api/v1/routes/gtfs-datasets/{id}/process-swiss?source_key=zurich" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

### Verify Route Data
```bash
# Check processing status
curl -X GET "http://localhost:8000/api/v1/routes/gtfs-datasets" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Get processed routes
curl -X GET "http://localhost:8000/api/v1/routes/transit-routes?route_type=3" \
  -H "Authorization: Bearer YOUR_TOKEN"
```

## 🚀 Deployment

### Docker Deployment
```dockerfile
FROM python:3.11-slim

# Install pfaedle dependencies
RUN apt-get update && apt-get install -y \
    build-essential cmake libzip-dev zlib1g-dev libbz2-dev \
    && rm -rf /var/lib/apt/lists/*

# Install pfaedle
RUN git clone --recurse-submodules https://github.com/ad-freiburg/pfaedle /tmp/pfaedle \
    && cd /tmp/pfaedle && mkdir build && cd build \
    && cmake .. && make -j && make install \
    && rm -rf /tmp/pfaedle

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Environment Variables
```bash
export ELETTRA_CONFIG_FILE="/app/config/production.yaml"
export POSTGRES_HOST="db.example.com"
export POSTGRES_PASSWORD="production_password"
```

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/pfaedle-enhancement`
3. Make changes and test with Swiss data
4. Submit a pull request

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- **[pfaedle](https://github.com/ad-freiburg/pfaedle)**: Precise map-matching for public transit feeds
- **[OpenTransportData.swiss](https://opentransportdata.swiss/)**: Swiss public transport data
- **[PostBus Switzerland](https://www.postbus.ch/)**: Swiss postal bus network data
- **[ZVV](https://www.zvv.ch/)**: Zurich transport network data

## 📞 Support

For issues related to:
- **Elettra Backend**: Create an issue in this repository
- **pfaedle**: See [pfaedle documentation](https://github.com/ad-freiburg/pfaedle)
- **Swiss Transport Data**: Check [OpenTransportData.swiss](https://opentransportdata.swiss/)

---

**Elettra**: Empowering Swiss electric bus transitions with data-driven insights 🚌⚡