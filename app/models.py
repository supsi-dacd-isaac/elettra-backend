from typing import Optional
import datetime
import decimal
import uuid

from sqlalchemy import CheckConstraint, Date, DateTime, Double, Enum, ForeignKeyConstraint, Index, Integer, Numeric, PrimaryKeyConstraint, REAL, String, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class GtfsAgencies(Base):
    __tablename__ = 'gtfs_agencies'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='gtfs_agency_pkey'),
        UniqueConstraint('gtfs_agency_id', name='gtfs_agency_gtfs_agency_id_key')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    gtfs_agency_id: Mapped[str] = mapped_column(Text, nullable=False)
    agency_name: Mapped[str] = mapped_column(Text, nullable=False)
    agency_url: Mapped[str] = mapped_column(Text, nullable=False)
    agency_timezone: Mapped[str] = mapped_column(Text, nullable=False)
    agency_lang: Mapped[Optional[str]] = mapped_column(Text)
    agency_phone: Mapped[Optional[str]] = mapped_column(Text)
    agency_fare_url: Mapped[Optional[str]] = mapped_column(Text)
    agency_email: Mapped[Optional[str]] = mapped_column(Text)

    gtfs_routes: Mapped[list['GtfsRoutes']] = relationship('GtfsRoutes', back_populates='agency')
    users: Mapped[list['Users']] = relationship('Users', back_populates='company')


class GtfsCalendar(Base):
    __tablename__ = 'gtfs_calendar'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='gtfs_calendar_pkey'),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    service_id: Mapped[str] = mapped_column(Text, nullable=False)
    monday: Mapped[int] = mapped_column(Integer, nullable=False)
    tuesday: Mapped[int] = mapped_column(Integer, nullable=False)
    wednesday: Mapped[int] = mapped_column(Integer, nullable=False)
    thursday: Mapped[int] = mapped_column(Integer, nullable=False)
    friday: Mapped[int] = mapped_column(Integer, nullable=False)
    saturday: Mapped[int] = mapped_column(Integer, nullable=False)
    sunday: Mapped[int] = mapped_column(Integer, nullable=False)
    start_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)
    end_date: Mapped[datetime.date] = mapped_column(Date, nullable=False)

    gtfs_trips: Mapped[list['GtfsTrips']] = relationship('GtfsTrips', back_populates='service')


class GtfsStops(Base):
    __tablename__ = 'gtfs_stops'
    __table_args__ = (
        CheckConstraint("stop_lat IS NULL OR stop_lat >= '-90'::integer::double precision AND stop_lat <= 90::double precision", name='stop_lat_check'),
        CheckConstraint("stop_lon IS NULL OR stop_lon >= '-180'::integer::double precision AND stop_lon <= 180::double precision", name='stop_lon_check'),
        PrimaryKeyConstraint('id', name='gtfs_stops_pkey')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    stop_id: Mapped[str] = mapped_column(Text, nullable=False)
    stop_code: Mapped[Optional[str]] = mapped_column(Text)
    stop_name: Mapped[Optional[str]] = mapped_column(Text)
    stop_desc: Mapped[Optional[str]] = mapped_column(Text)
    stop_lat: Mapped[Optional[float]] = mapped_column(Double(53))
    stop_lon: Mapped[Optional[float]] = mapped_column(Double(53))
    zone_id: Mapped[Optional[str]] = mapped_column(Text)
    stop_url: Mapped[Optional[str]] = mapped_column(Text)
    location_type: Mapped[Optional[int]] = mapped_column(Integer)
    parent_station: Mapped[Optional[str]] = mapped_column(Text)
    stop_timezone: Mapped[Optional[str]] = mapped_column(Text)
    wheelchair_boarding: Mapped[Optional[int]] = mapped_column(Integer)
    platform_code: Mapped[Optional[str]] = mapped_column(Text)
    level_id: Mapped[Optional[str]] = mapped_column(Text)

    depots: Mapped[list['Depots']] = relationship('Depots', back_populates='stop')
    gtfs_stops_times: Mapped[list['GtfsStopsTimes']] = relationship('GtfsStopsTimes', back_populates='stop')


class WeatherMeasurements(Base):
    __tablename__ = 'weather_measurements'
    __table_args__ = (
        CheckConstraint("latitude >= '-90'::integer::numeric AND latitude <= 90::numeric", name='ck_lat_range'),
        CheckConstraint("longitude >= '-180'::integer::numeric AND longitude <= 180::numeric", name='ck_lon_range'),
        CheckConstraint('pressure > 0', name='weather_measurements_pressure_check'),
        CheckConstraint('relative_humidity >= 0::double precision AND relative_humidity <= 100::double precision', name='weather_measurements_relative_humidity_check'),
        CheckConstraint('wind_direction >= 0::double precision AND wind_direction < 360::double precision', name='weather_measurements_wind_direction_check'),
        CheckConstraint('wind_speed >= 0::double precision', name='weather_measurements_wind_speed_check'),
        PrimaryKeyConstraint('id', name='weather_measurements_pkey'),
        UniqueConstraint('time_utc', 'latitude', 'longitude', name='uq_weather_time_lat_lon'),
        Index('ix_weather_lat_lon', 'latitude', 'longitude'),
        Index('ix_weather_time_brin', 'time_utc')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    time_utc: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False)
    latitude: Mapped[decimal.Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    longitude: Mapped[decimal.Decimal] = mapped_column(Numeric(9, 5), nullable=False)
    temp_air: Mapped[Optional[float]] = mapped_column(REAL)
    relative_humidity: Mapped[Optional[float]] = mapped_column(REAL)
    ghi: Mapped[Optional[float]] = mapped_column(REAL)
    dni: Mapped[Optional[float]] = mapped_column(REAL)
    dhi: Mapped[Optional[float]] = mapped_column(REAL)
    ir_h: Mapped[Optional[float]] = mapped_column(REAL)
    wind_speed: Mapped[Optional[float]] = mapped_column(REAL)
    wind_direction: Mapped[Optional[float]] = mapped_column(REAL)
    pressure: Mapped[Optional[int]] = mapped_column(Integer)


class GtfsRoutes(Base):
    __tablename__ = 'gtfs_routes'
    __table_args__ = (
        ForeignKeyConstraint(['agency_id'], ['gtfs_agencies.id'], name='gtfs_routes_agency_id_fkey'),
        PrimaryKeyConstraint('id', name='gtfs_routes_pkey')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    route_id: Mapped[str] = mapped_column(Text, nullable=False)
    agency_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    route_short_name: Mapped[Optional[str]] = mapped_column(Text)
    route_long_name: Mapped[Optional[str]] = mapped_column(Text)
    route_desc: Mapped[Optional[str]] = mapped_column(Text)
    route_type: Mapped[Optional[int]] = mapped_column(Integer)
    route_url: Mapped[Optional[str]] = mapped_column(Text)
    route_color: Mapped[Optional[str]] = mapped_column(Text)
    route_text_color: Mapped[Optional[str]] = mapped_column(Text)
    route_sort_order: Mapped[Optional[int]] = mapped_column(Integer)
    continuous_pickup: Mapped[Optional[int]] = mapped_column(Integer)
    continuous_drop_off: Mapped[Optional[int]] = mapped_column(Integer)

    agency: Mapped['GtfsAgencies'] = relationship('GtfsAgencies', back_populates='gtfs_routes')
    gtfs_trips: Mapped[list['GtfsTrips']] = relationship('GtfsTrips', back_populates='route')
    variants: Mapped[list['Variants']] = relationship('Variants', back_populates='route')


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("role = ANY (ARRAY['admin'::text, 'analyst'::text, 'viewer'::text])", name='users_role_check'),
        ForeignKeyConstraint(['company_id'], ['gtfs_agencies.id'], ondelete='CASCADE', name='users_gtfs_agencies_id_fkey'),
        PrimaryKeyConstraint('id', name='users_pkey'),
        UniqueConstraint('email', name='users_email_key')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    email: Mapped[str] = mapped_column(Text, nullable=False)
    full_name: Mapped[str] = mapped_column(Text, nullable=False)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)
    role: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))

    company: Mapped['GtfsAgencies'] = relationship('GtfsAgencies', back_populates='users')
    buses_models: Mapped[list['BusesModels']] = relationship('BusesModels', back_populates='user')
    depots: Mapped[list['Depots']] = relationship('Depots', back_populates='user')
    buses: Mapped[list['Buses']] = relationship('Buses', back_populates='user')
    simulation_runs: Mapped[list['SimulationRuns']] = relationship('SimulationRuns', back_populates='user')


class BusesModels(Base):
    __tablename__ = 'buses_models'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='buses_models_users_fk'),
        PrimaryKeyConstraint('id', name='buses_models_pkey'),
        UniqueConstraint('name', 'user_id', name='user_buses_models_name_unique')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    specs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    manufacturer: Mapped[Optional[str]] = mapped_column(Text)
    description: Mapped[Optional[str]] = mapped_column(String)

    user: Mapped['Users'] = relationship('Users', back_populates='buses_models')
    buses: Mapped[list['Buses']] = relationship('Buses', back_populates='bus_model')


class Depots(Base):
    __tablename__ = 'depots'
    __table_args__ = (
        ForeignKeyConstraint(['stop_id'], ['gtfs_stops.id'], ondelete='CASCADE', onupdate='CASCADE', name='depots_gtfs_stops_fk'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='depots_users_fk'),
        PrimaryKeyConstraint('id', name='depots_pkey'),
        Index('depots_agency_id_idx', 'user_id')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    address: Mapped[Optional[str]] = mapped_column(Text)
    features: Mapped[Optional[dict]] = mapped_column(JSONB)
    stop_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    stop: Mapped[Optional['GtfsStops']] = relationship('GtfsStops', back_populates='depots')
    user: Mapped['Users'] = relationship('Users', back_populates='depots')


class GtfsTrips(Base):
    __tablename__ = 'gtfs_trips'
    __table_args__ = (
        ForeignKeyConstraint(['route_id'], ['gtfs_routes.id'], name='gtfs_trips_route_id_fkey'),
        ForeignKeyConstraint(['service_id'], ['gtfs_calendar.id'], name='gtfs_trips_service_fk'),
        PrimaryKeyConstraint('id', name='gtfs_trips_pkey'),
        Index('gtfs_trips_trip_id_udx', 'trip_id', unique=True)
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    route_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    gtfs_service_id: Mapped[str] = mapped_column(Text, nullable=False)
    trip_id: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Enum('gtfs', 'depot', 'school', 'service', 'other', 'transfer', name='trip_status'), nullable=False, server_default=text("'gtfs'::trip_status"))
    trip_headsign: Mapped[Optional[str]] = mapped_column(Text)
    trip_short_name: Mapped[Optional[str]] = mapped_column(Text)
    direction_id: Mapped[Optional[int]] = mapped_column(Integer)
    block_id: Mapped[Optional[str]] = mapped_column(Text)
    shape_id: Mapped[Optional[str]] = mapped_column(Text)
    wheelchair_accessible: Mapped[Optional[int]] = mapped_column(Integer)
    bikes_allowed: Mapped[Optional[int]] = mapped_column(Integer)
    start_stop_name: Mapped[Optional[str]] = mapped_column(String)
    end_stop_name: Mapped[Optional[str]] = mapped_column(String)
    departure_time: Mapped[Optional[str]] = mapped_column(String)
    arrival_time: Mapped[Optional[str]] = mapped_column(String)

    route: Mapped['GtfsRoutes'] = relationship('GtfsRoutes', back_populates='gtfs_trips')
    service: Mapped['GtfsCalendar'] = relationship('GtfsCalendar', back_populates='gtfs_trips')
    gtfs_stops_times: Mapped[list['GtfsStopsTimes']] = relationship('GtfsStopsTimes', back_populates='trip')
    shifts_structures: Mapped[list['ShiftsStructures']] = relationship('ShiftsStructures', back_populates='trip')


class Variants(Base):
    __tablename__ = 'variants'
    __table_args__ = (
        CheckConstraint('variant_num > 0', name='variants_variant_num_check'),
        ForeignKeyConstraint(['route_id'], ['gtfs_routes.id'], ondelete='CASCADE', name='variants_gtfs_routes_id_fkey'),
        PrimaryKeyConstraint('id', name='variants_pkey'),
        UniqueConstraint('route_id', 'variant_num', name='variants_route_variant_key'),
        Index('variants_route_id_idx', 'route_id')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    route_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    variant_num: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    shape_id: Mapped[str] = mapped_column(String, nullable=False)

    route: Mapped['GtfsRoutes'] = relationship('GtfsRoutes', back_populates='variants')
    simulation_runs: Mapped[list['SimulationRuns']] = relationship('SimulationRuns', back_populates='variant')


class Buses(Base):
    __tablename__ = 'buses'
    __table_args__ = (
        ForeignKeyConstraint(['bus_model_id'], ['buses_models.id'], ondelete='RESTRICT', onupdate='CASCADE', name='buses_bus_model_id_fkey'),
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE', name='buses_users_fk'),
        PrimaryKeyConstraint('id', name='buses_pkey'),
        UniqueConstraint('user_id', 'name', name='bus_name_user_id_unique'),
        Index('idx_buses_bus_model_id', 'bus_model_id')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    specs: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    bus_model_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    bus_model: Mapped[Optional['BusesModels']] = relationship('BusesModels', back_populates='buses')
    user: Mapped['Users'] = relationship('Users', back_populates='buses')
    shifts: Mapped[list['Shifts']] = relationship('Shifts', back_populates='bus')


class GtfsStopsTimes(Base):
    __tablename__ = 'gtfs_stops_times'
    __table_args__ = (
        ForeignKeyConstraint(['stop_id'], ['gtfs_stops.id'], name='gtfs_stops_times_stop_id_fkey'),
        ForeignKeyConstraint(['trip_id'], ['gtfs_trips.id'], name='gtfs_stops_times_trip_id_fkey'),
        PrimaryKeyConstraint('id', name='gtfs_stops_times_pkey'),
        Index('gtfs_stops_times_trip_seq_udx', 'trip_id', 'stop_sequence', unique=True)
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    stop_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    arrival_time: Mapped[Optional[str]] = mapped_column(Text)
    departure_time: Mapped[Optional[str]] = mapped_column(Text)
    stop_sequence: Mapped[Optional[int]] = mapped_column(Integer)
    stop_headsign: Mapped[Optional[str]] = mapped_column(Text)
    pickup_type: Mapped[Optional[int]] = mapped_column(Integer)
    drop_off_type: Mapped[Optional[int]] = mapped_column(Integer)
    shape_dist_traveled: Mapped[Optional[float]] = mapped_column(Double(53))
    timepoint: Mapped[Optional[int]] = mapped_column(Integer)
    continuous_pickup: Mapped[Optional[int]] = mapped_column(Integer)
    continuous_drop_off: Mapped[Optional[int]] = mapped_column(Integer)

    stop: Mapped['GtfsStops'] = relationship('GtfsStops', back_populates='gtfs_stops_times')
    trip: Mapped['GtfsTrips'] = relationship('GtfsTrips', back_populates='gtfs_stops_times')


class SimulationRuns(Base):
    __tablename__ = 'simulation_runs'
    __table_args__ = (
        ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='SET NULL', name='simulation_runs_user_id_fkey'),
        ForeignKeyConstraint(['variant_id'], ['variants.id'], ondelete='CASCADE', name='simulation_runs_variant_id_fkey'),
        PrimaryKeyConstraint('id', name='simulation_runs_pkey'),
        Index('simulation_runs_variant_id_idx', 'variant_id')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    input_params: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Enum('pending', 'running', 'completed', 'failed', name='sim_status'), nullable=False, server_default=text("'pending'::sim_status"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    variant_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    optimal_battery_kwh: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric)
    output_results: Mapped[Optional[dict]] = mapped_column(JSONB)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    user: Mapped['Users'] = relationship('Users', back_populates='simulation_runs')
    variant: Mapped['Variants'] = relationship('Variants', back_populates='simulation_runs')


class Shifts(Base):
    __tablename__ = 'shifts'
    __table_args__ = (
        ForeignKeyConstraint(['bus_id'], ['buses.id'], ondelete='CASCADE', onupdate='CASCADE', name='shifts_bus_id_fkey'),
        PrimaryKeyConstraint('id', name='shifts_pkey'),
        Index('idx_shifts_bus_id', 'bus_id'),
        Index('shifts_name_idx', 'name')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    bus_id: Mapped[Optional[uuid.UUID]] = mapped_column(Uuid)

    bus: Mapped[Optional['Buses']] = relationship('Buses', back_populates='shifts')
    shifts_structures: Mapped[list['ShiftsStructures']] = relationship('ShiftsStructures', back_populates='shift')


class ShiftsStructures(Base):
    __tablename__ = 'shifts_structures'
    __table_args__ = (
        ForeignKeyConstraint(['shift_id'], ['shifts.id'], ondelete='CASCADE', onupdate='CASCADE', name='shifts_structures_shift_fk'),
        ForeignKeyConstraint(['trip_id'], ['gtfs_trips.id'], ondelete='CASCADE', onupdate='CASCADE', name='shifts_structures_trip_fk'),
        PrimaryKeyConstraint('id', name='shifts_structures_pkey'),
        UniqueConstraint('trip_id', 'shift_id', 'sequence_number', name='shifts_structures_unique'),
        Index('shifts_structures_seq_idx', 'sequence_number'),
        Index('shifts_structures_shift_idx', 'shift_id'),
        Index('shifts_structures_trip_idx', 'trip_id')
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    shift_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)

    shift: Mapped['Shifts'] = relationship('Shifts', back_populates='shifts_structures')
    trip: Mapped['GtfsTrips'] = relationship('GtfsTrips', back_populates='shifts_structures')
