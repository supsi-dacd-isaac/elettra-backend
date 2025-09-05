from typing import List, Optional

from sqlalchemy import CheckConstraint, Date, DateTime, Double, Enum, ForeignKeyConstraint, Index, Integer, Numeric, PrimaryKeyConstraint, Text, UniqueConstraint, Uuid, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
import datetime
import decimal
import uuid

class Base(DeclarativeBase):
    pass


class GtfsAgencies(Base):
    __tablename__ = 'gtfs_agencies'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='gtfs_agency_pkey'),
        UniqueConstraint('gtfs_agency_id', name='gtfs_agency_gtfs_agency_id_key'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    gtfs_agency_id: Mapped[str] = mapped_column(Text)
    agency_name: Mapped[str] = mapped_column(Text)
    agency_url: Mapped[str] = mapped_column(Text)
    agency_timezone: Mapped[str] = mapped_column(Text)
    agency_lang: Mapped[Optional[str]] = mapped_column(Text)
    agency_phone: Mapped[Optional[str]] = mapped_column(Text)
    agency_fare_url: Mapped[Optional[str]] = mapped_column(Text)
    agency_email: Mapped[Optional[str]] = mapped_column(Text)

    bus_models: Mapped[List['BusModels']] = relationship('BusModels', back_populates='company')
    gtfs_routes: Mapped[List['GtfsRoutes']] = relationship('GtfsRoutes', back_populates='agency')
    users: Mapped[List['Users']] = relationship('Users', back_populates='company')


class GtfsCalendar(Base):
    __tablename__ = 'gtfs_calendar'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='gtfs_calendar_pkey'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    service_id: Mapped[str] = mapped_column(Text)
    monday: Mapped[int] = mapped_column(Integer)
    tuesday: Mapped[int] = mapped_column(Integer)
    wednesday: Mapped[int] = mapped_column(Integer)
    thursday: Mapped[int] = mapped_column(Integer)
    friday: Mapped[int] = mapped_column(Integer)
    saturday: Mapped[int] = mapped_column(Integer)
    sunday: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[datetime.date] = mapped_column(Date)
    end_date: Mapped[datetime.date] = mapped_column(Date)

    gtfs_trips: Mapped[List['GtfsTrips']] = relationship('GtfsTrips', back_populates='service')


class GtfsStops(Base):
    __tablename__ = 'gtfs_stops'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='gtfs_stops_pkey'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    stop_id: Mapped[str] = mapped_column(Text)
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

    gtfs_stops_times: Mapped[List['GtfsStopsTimes']] = relationship('GtfsStopsTimes', back_populates='stop')


class BusModels(Base):
    __tablename__ = 'bus_models'
    __table_args__ = (
        ForeignKeyConstraint(['company_id'], ['public.gtfs_agencies.id'], ondelete='CASCADE', name='bus_models_company_id_fkey'),
        PrimaryKeyConstraint('id', name='bus_models_pkey'),
        UniqueConstraint('company_id', 'name', name='bus_models_company_id_name_key'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    name: Mapped[str] = mapped_column(Text)
    specs: Mapped[dict] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    manufacturer: Mapped[Optional[str]] = mapped_column(Text)

    company: Mapped['GtfsAgencies'] = relationship('GtfsAgencies', back_populates='bus_models')


class GtfsRoutes(Base):
    __tablename__ = 'gtfs_routes'
    __table_args__ = (
        ForeignKeyConstraint(['agency_id'], ['public.gtfs_agencies.id'], name='gtfs_routes_agency_id_fkey'),
        PrimaryKeyConstraint('id', name='gtfs_routes_pkey'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    route_id: Mapped[str] = mapped_column(Text)
    agency_id: Mapped[uuid.UUID] = mapped_column(Uuid)
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
    gtfs_trips: Mapped[List['GtfsTrips']] = relationship('GtfsTrips', back_populates='route')
    variants: Mapped[List['Variants']] = relationship('Variants', back_populates='route')


class Users(Base):
    __tablename__ = 'users'
    __table_args__ = (
        CheckConstraint("role = ANY (ARRAY['admin'::text, 'analyst'::text, 'viewer'::text])", name='users_role_check'),
        ForeignKeyConstraint(['company_id'], ['public.gtfs_agencies.id'], ondelete='CASCADE', name='users_gtfs_agencies_id_fkey'),
        PrimaryKeyConstraint('id', name='users_pkey'),
        UniqueConstraint('email', name='users_email_key'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    company_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    email: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(Text)
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text('now()'))

    company: Mapped['GtfsAgencies'] = relationship('GtfsAgencies', back_populates='users')
    simulation_runs: Mapped[List['SimulationRuns']] = relationship('SimulationRuns', back_populates='user')


class GtfsTrips(Base):
    __tablename__ = 'gtfs_trips'
    __table_args__ = (
        ForeignKeyConstraint(['route_id'], ['public.gtfs_routes.id'], name='gtfs_trips_route_id_fkey'),
        ForeignKeyConstraint(['service_id'], ['public.gtfs_calendar.id'], name='gtfs_trips_service_fk'),
        PrimaryKeyConstraint('id', name='gtfs_trips_pkey'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    route_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    service_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    gtfs_service_id: Mapped[str] = mapped_column(Text)
    trip_id: Mapped[str] = mapped_column(Text)
    trip_headsign: Mapped[Optional[str]] = mapped_column(Text)
    trip_short_name: Mapped[Optional[str]] = mapped_column(Text)
    direction_id: Mapped[Optional[int]] = mapped_column(Integer)
    block_id: Mapped[Optional[str]] = mapped_column(Text)
    shape_id: Mapped[Optional[str]] = mapped_column(Text)
    wheelchair_accessible: Mapped[Optional[int]] = mapped_column(Integer)
    bikes_allowed: Mapped[Optional[int]] = mapped_column(Integer)

    route: Mapped['GtfsRoutes'] = relationship('GtfsRoutes', back_populates='gtfs_trips')
    service: Mapped['GtfsCalendar'] = relationship('GtfsCalendar', back_populates='gtfs_trips')
    gtfs_stops_times: Mapped[List['GtfsStopsTimes']] = relationship('GtfsStopsTimes', back_populates='trip')


class Variants(Base):
    __tablename__ = 'variants'
    __table_args__ = (
        CheckConstraint('variant_num > 0', name='variants_variant_num_check'),
        ForeignKeyConstraint(['route_id'], ['public.gtfs_routes.id'], ondelete='CASCADE', name='variants_gtfs_routes_id_fkey'),
        PrimaryKeyConstraint('id', name='variants_pkey'),
        UniqueConstraint('route_id', 'variant_num', name='variants_route_variant_key'),
        Index('variants_route_id_idx', 'route_id'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    route_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    variant_num: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text('now()'))

    route: Mapped['GtfsRoutes'] = relationship('GtfsRoutes', back_populates='variants')
    simulation_runs: Mapped[List['SimulationRuns']] = relationship('SimulationRuns', back_populates='variant')


class GtfsStopsTimes(Base):
    __tablename__ = 'gtfs_stops_times'
    __table_args__ = (
        ForeignKeyConstraint(['stop_id'], ['public.gtfs_stops.id'], name='gtfs_stops_times_stop_id_fkey'),
        ForeignKeyConstraint(['trip_id'], ['public.gtfs_trips.id'], name='gtfs_stops_times_trip_id_fkey'),
        PrimaryKeyConstraint('id', name='gtfs_stops_times_pkey'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    stop_id: Mapped[uuid.UUID] = mapped_column(Uuid)
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
        ForeignKeyConstraint(['user_id'], ['public.users.id'], ondelete='SET NULL', name='simulation_runs_user_id_fkey'),
        ForeignKeyConstraint(['variant_id'], ['public.variants.id'], ondelete='CASCADE', name='simulation_runs_variant_id_fkey'),
        PrimaryKeyConstraint('id', name='simulation_runs_pkey'),
        Index('simulation_runs_variant_id_idx', 'variant_id'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    input_params: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(Enum('pending', 'running', 'completed', 'failed', name='sim_status'), server_default=text("'pending'::sim_status"))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), server_default=text('now()'))
    variant_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    optimal_battery_kwh: Mapped[Optional[decimal.Decimal]] = mapped_column(Numeric)
    output_results: Mapped[Optional[dict]] = mapped_column(JSONB)
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime(True))

    user: Mapped['Users'] = relationship('Users', back_populates='simulation_runs')
    variant: Mapped['Variants'] = relationship('Variants', back_populates='simulation_runs')


class WeatherMeasurements(Base):
    __tablename__ = 'weather_measurements'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='weather_measurements_pkey'),
        UniqueConstraint('time_utc', 'latitude', 'longitude', name='uq_weather_time_lat_lon'),
        CheckConstraint('relative_humidity BETWEEN 0 AND 100', name='weather_measurements_relative_humidity_check'),
        CheckConstraint('wind_speed >= 0', name='weather_measurements_wind_speed_check'),
        CheckConstraint('wind_direction >= 0 AND wind_direction < 360', name='weather_measurements_wind_direction_check'),
        CheckConstraint('pressure > 0', name='weather_measurements_pressure_check'),
        CheckConstraint('latitude BETWEEN -90 AND 90', name='ck_lat_range'),
        CheckConstraint('longitude BETWEEN -180 AND 180', name='ck_lon_range'),
        Index('ix_weather_time_brin', 'time_utc'),
        Index('ix_weather_lat_lon', 'latitude', 'longitude'),
        {'schema': 'public'}
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, server_default=text('gen_random_uuid()'))
    time_utc: Mapped[datetime.datetime] = mapped_column(DateTime(True))
    latitude: Mapped[decimal.Decimal] = mapped_column(Numeric(8, 5))
    longitude: Mapped[decimal.Decimal] = mapped_column(Numeric(9, 5))
    temp_air: Mapped[Optional[float]] = mapped_column(Double(53))
    relative_humidity: Mapped[Optional[float]] = mapped_column(Double(53))
    ghi: Mapped[Optional[float]] = mapped_column(Double(53))
    dni: Mapped[Optional[float]] = mapped_column(Double(53))
    dhi: Mapped[Optional[float]] = mapped_column(Double(53))
    ir_h: Mapped[Optional[float]] = mapped_column(Double(53))
    wind_speed: Mapped[Optional[float]] = mapped_column(Double(53))
    wind_direction: Mapped[Optional[float]] = mapped_column(Double(53))
    pressure: Mapped[Optional[int]] = mapped_column(Integer)

