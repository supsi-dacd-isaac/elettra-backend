--
-- PostgreSQL database dump
--

\restrict bSUlLl6GHjK5gJeZ0c9qtfbC1IIhmMf26YTxN0VOt1wbVzVyJgGPF5fYGWbPrxx

-- Dumped from database version 16.10
-- Dumped by pg_dump version 16.10 (Ubuntu 16.10-0ubuntu0.24.04.1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: pgcrypto; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS pgcrypto WITH SCHEMA public;


--
-- Name: EXTENSION pgcrypto; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION pgcrypto IS 'cryptographic functions';


--
-- Name: sim_status; Type: TYPE; Schema: public; Owner: admin
--

CREATE TYPE public.sim_status AS ENUM (
    'pending',
    'running',
    'completed',
    'failed'
);


ALTER TYPE public.sim_status OWNER TO admin;

--
-- Name: simstatus; Type: TYPE; Schema: public; Owner: admin
--

CREATE TYPE public.simstatus AS ENUM (
    'pending',
    'running',
    'completed',
    'failed'
);


ALTER TYPE public.simstatus OWNER TO admin;

--
-- Name: trip_status; Type: TYPE; Schema: public; Owner: admin
--

CREATE TYPE public.trip_status AS ENUM (
    'gtfs',
    'depot',
    'school',
    'service',
    'other',
    'transfer'
);


ALTER TYPE public.trip_status OWNER TO admin;

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: buses; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.buses (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    specs jsonb DEFAULT '{}'::jsonb NOT NULL,
    bus_model_id uuid
);


ALTER TABLE public.buses OWNER TO admin;

--
-- Name: buses_models; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.buses_models (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    manufacturer text,
    specs jsonb DEFAULT '{}'::jsonb NOT NULL,
    description character varying,
    user_id uuid NOT NULL
);


ALTER TABLE public.buses_models OWNER TO admin;

--
-- Name: depots; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.depots (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    name text NOT NULL,
    address text,
    features jsonb,
    stop_id uuid
);


ALTER TABLE public.depots OWNER TO admin;

--
-- Name: gtfs_agencies; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.gtfs_agencies (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    gtfs_agency_id text NOT NULL,
    agency_name text NOT NULL,
    agency_url text NOT NULL,
    agency_timezone text NOT NULL,
    agency_lang text,
    agency_phone text,
    agency_fare_url text,
    agency_email text
);


ALTER TABLE public.gtfs_agencies OWNER TO admin;

--
-- Name: gtfs_calendar; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.gtfs_calendar (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    service_id text NOT NULL,
    monday integer NOT NULL,
    tuesday integer NOT NULL,
    wednesday integer NOT NULL,
    thursday integer NOT NULL,
    friday integer NOT NULL,
    saturday integer NOT NULL,
    sunday integer NOT NULL,
    start_date date NOT NULL,
    end_date date NOT NULL
);


ALTER TABLE public.gtfs_calendar OWNER TO admin;

--
-- Name: gtfs_routes; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.gtfs_routes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    route_id text NOT NULL,
    agency_id uuid NOT NULL,
    route_short_name text,
    route_long_name text,
    route_desc text,
    route_type integer,
    route_url text,
    route_color text,
    route_text_color text,
    route_sort_order integer,
    continuous_pickup integer,
    continuous_drop_off integer,
    gtfs_file_date date DEFAULT '2025-04-14'::date NOT NULL,
    gtfs_year integer DEFAULT 2025 NOT NULL
);


ALTER TABLE public.gtfs_routes OWNER TO admin;

--
-- Name: gtfs_stops; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.gtfs_stops (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    stop_id text NOT NULL,
    stop_code text,
    stop_name text,
    stop_desc text,
    stop_lat double precision,
    stop_lon double precision,
    zone_id text,
    stop_url text,
    location_type integer,
    parent_station text,
    stop_timezone text,
    wheelchair_boarding integer,
    platform_code text,
    level_id text,
    CONSTRAINT stop_lat_check CHECK (((stop_lat IS NULL) OR ((stop_lat >= ('-90'::integer)::double precision) AND (stop_lat <= (90)::double precision)))),
    CONSTRAINT stop_lon_check CHECK (((stop_lon IS NULL) OR ((stop_lon >= ('-180'::integer)::double precision) AND (stop_lon <= (180)::double precision))))
);


ALTER TABLE public.gtfs_stops OWNER TO admin;

--
-- Name: gtfs_stops_times; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.gtfs_stops_times (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trip_id uuid NOT NULL,
    arrival_time text,
    departure_time text,
    stop_id uuid NOT NULL,
    stop_sequence integer,
    stop_headsign text,
    pickup_type integer,
    drop_off_type integer,
    shape_dist_traveled double precision,
    timepoint integer,
    continuous_pickup integer,
    continuous_drop_off integer
);


ALTER TABLE public.gtfs_stops_times OWNER TO admin;

--
-- Name: gtfs_trips; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.gtfs_trips (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    route_id uuid NOT NULL,
    service_id uuid NOT NULL,
    gtfs_service_id text NOT NULL,
    trip_id text NOT NULL,
    trip_headsign text,
    trip_short_name text,
    direction_id integer,
    block_id text,
    shape_id text,
    wheelchair_accessible integer,
    bikes_allowed integer,
    start_stop_name character varying,
    end_stop_name character varying,
    departure_time character varying,
    arrival_time character varying,
    status public.trip_status DEFAULT 'gtfs'::public.trip_status NOT NULL
);


ALTER TABLE public.gtfs_trips OWNER TO admin;

--
-- Name: shifts; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.shifts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    bus_id uuid
);


ALTER TABLE public.shifts OWNER TO admin;

--
-- Name: shifts_structures; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.shifts_structures (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    trip_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    sequence_number integer NOT NULL
);


ALTER TABLE public.shifts_structures OWNER TO admin;

--
-- Name: prediction_runs; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.prediction_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    shift_id uuid NOT NULL,
    bus_model_id uuid NOT NULL,
    yearly_analysis_id uuid,
    model_name text NOT NULL,
    external_temp_celsius numeric NOT NULL,
    auxiliary_heating_type text DEFAULT 'default' NOT NULL,
    occupancy_percent numeric DEFAULT 50 NOT NULL,
    contextual_parameters jsonb,
    summary jsonb,
    status text DEFAULT 'pending' NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone
);

ALTER TABLE public.prediction_runs OWNER TO admin;

--
-- Name: trip_predictions; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.trip_predictions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    prediction_run_id uuid NOT NULL,
    trip_id uuid NOT NULL,
    sequence_number integer NOT NULL,
    prediction_kwh numeric NOT NULL,
    prediction_median_kwh numeric,
    drivetrain_kwh numeric,
    auxiliary_kwh numeric,
    mass_sensitivity_kwh_per_kwh_batt numeric,
    quantiles jsonb
);

ALTER TABLE public.trip_predictions OWNER TO admin;

--
-- Name: optimization_runs; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.optimization_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    bus_model_id uuid NOT NULL,
    mode text NOT NULL,
    status text DEFAULT 'pending' NOT NULL,
    input_params jsonb NOT NULL,
    prediction_run_ids jsonb,
    results jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    CONSTRAINT optimization_runs_mode_check CHECK ((mode = ANY (ARRAY['battery_only'::text, 'charging_only'::text, 'joint'::text])))
);

ALTER TABLE public.optimization_runs OWNER TO admin;

--
-- Name: users; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    company_id uuid NOT NULL,
    email text NOT NULL,
    full_name text NOT NULL,
    password_hash text NOT NULL,
    role text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT users_role_check CHECK ((role = ANY (ARRAY['admin'::text, 'analyst'::text, 'viewer'::text])))
);


ALTER TABLE public.users OWNER TO admin;

--
-- Name: variants; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.variants (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    route_id uuid NOT NULL,
    variant_num integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    shape_id character varying NOT NULL,
    CONSTRAINT variants_variant_num_check CHECK ((variant_num > 0))
);


ALTER TABLE public.variants OWNER TO admin;

--
-- Name: weather_temperature_clusters; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.weather_temperature_clusters (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    latitude numeric(8,5) NOT NULL,
    longitude numeric(9,5) NOT NULL,
    k integer NOT NULL,
    start_time character varying(5) NOT NULL,
    end_time character varying(5) NOT NULL,
    cluster_id integer NOT NULL,
    centroid_daily_avg_temp real NOT NULL,
    occurrences integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    CONSTRAINT ck_wtc_lat_range CHECK (((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric))),
    CONSTRAINT ck_wtc_lon_range CHECK (((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric)))
);


ALTER TABLE public.weather_temperature_clusters OWNER TO admin;

--
-- Name: weather_measurements; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.weather_measurements (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    time_utc timestamp with time zone NOT NULL,
    latitude numeric(8,5) NOT NULL,
    longitude numeric(9,5) NOT NULL,
    temp_air real,
    relative_humidity real,
    ghi real,
    dni real,
    dhi real,
    ir_h real,
    wind_speed real,
    wind_direction real,
    pressure integer,
    CONSTRAINT ck_lat_range CHECK (((latitude >= ('-90'::integer)::numeric) AND (latitude <= (90)::numeric))),
    CONSTRAINT ck_lon_range CHECK (((longitude >= ('-180'::integer)::numeric) AND (longitude <= (180)::numeric))),
    CONSTRAINT weather_measurements_pressure_check CHECK ((pressure > 0)),
    CONSTRAINT weather_measurements_relative_humidity_check CHECK (((relative_humidity >= (0)::double precision) AND (relative_humidity <= (100)::double precision))),
    CONSTRAINT weather_measurements_wind_direction_check CHECK (((wind_direction >= (0)::double precision) AND (wind_direction < (360)::double precision))),
    CONSTRAINT weather_measurements_wind_speed_check CHECK ((wind_speed >= (0)::double precision))
);


ALTER TABLE public.weather_measurements OWNER TO admin;

--
-- Name: yearly_analysis; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.yearly_analysis (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    optimization_run_id uuid,
    name text NOT NULL,
    features jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


ALTER TABLE public.yearly_analysis OWNER TO admin;

--
-- Name: buses_manufacturers; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.buses_manufacturers (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL
);


ALTER TABLE public.buses_manufacturers OWNER TO admin;

--
-- Name: buses_models_refs; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.buses_models_refs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    buses_manufacturer_id uuid NOT NULL
);


ALTER TABLE public.buses_models_refs OWNER TO admin;

--
-- Name: buses_lca_data; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.buses_lca_data (
    id uuid NOT NULL,
    source_id integer NOT NULL,
    name text NOT NULL,
    description text,
    traffic_characteristics text,
    functional_unit character varying NOT NULL,
    size character varying,
    year integer NOT NULL,
    vehicle_subtype character varying,
    powertrain character varying,
    geography character varying,
    passenger_capacity numeric,
    active boolean NOT NULL
);


ALTER TABLE public.buses_lca_data OWNER TO admin;

--
-- Name: buses bus_name_user_id_unique; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses
    ADD CONSTRAINT bus_name_user_id_unique UNIQUE (user_id, name);


--
-- Name: buses_models buses_models_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_models
    ADD CONSTRAINT buses_models_pkey PRIMARY KEY (id);


--
-- Name: buses buses_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses
    ADD CONSTRAINT buses_pkey PRIMARY KEY (id);


--
-- Name: depots depots_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.depots
    ADD CONSTRAINT depots_pkey PRIMARY KEY (id);


--
-- Name: gtfs_agencies gtfs_agency_gtfs_agency_id_key; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_agencies
    ADD CONSTRAINT gtfs_agency_gtfs_agency_id_key UNIQUE (gtfs_agency_id);


--
-- Name: gtfs_agencies gtfs_agency_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_agencies
    ADD CONSTRAINT gtfs_agency_pkey PRIMARY KEY (id);


--
-- Name: gtfs_calendar gtfs_calendar_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_calendar
    ADD CONSTRAINT gtfs_calendar_pkey PRIMARY KEY (id);


--
-- Name: gtfs_routes gtfs_routes_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_routes
    ADD CONSTRAINT gtfs_routes_pkey PRIMARY KEY (id);


--
-- Name: gtfs_stops gtfs_stops_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_stops
    ADD CONSTRAINT gtfs_stops_pkey PRIMARY KEY (id);


--
-- Name: gtfs_stops_times gtfs_stops_times_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_stops_times
    ADD CONSTRAINT gtfs_stops_times_pkey PRIMARY KEY (id);


--
-- Name: gtfs_trips gtfs_trips_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_trips
    ADD CONSTRAINT gtfs_trips_pkey PRIMARY KEY (id);


--
-- Name: shifts shifts_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_pkey PRIMARY KEY (id);


--
-- Name: shifts_structures shifts_structures_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.shifts_structures
    ADD CONSTRAINT shifts_structures_pkey PRIMARY KEY (id);


--
-- Name: shifts_structures shifts_structures_unique; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.shifts_structures
    ADD CONSTRAINT shifts_structures_unique UNIQUE (trip_id, shift_id, sequence_number);


--
-- Name: prediction_runs prediction_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.prediction_runs
    ADD CONSTRAINT prediction_runs_pkey PRIMARY KEY (id);


--
-- Name: optimization_runs optimization_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.optimization_runs
    ADD CONSTRAINT optimization_runs_pkey PRIMARY KEY (id);


--
-- Name: trip_predictions trip_predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.trip_predictions
    ADD CONSTRAINT trip_predictions_pkey PRIMARY KEY (id);


--
-- Name: weather_measurements uq_weather_time_lat_lon; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.weather_measurements
    ADD CONSTRAINT uq_weather_time_lat_lon UNIQUE (time_utc, latitude, longitude);


--
-- Name: buses_models user_buses_models_name_unique; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_models
    ADD CONSTRAINT user_buses_models_name_unique UNIQUE (name, user_id);


--
-- Name: users users_email_key; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_key UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: variants variants_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.variants
    ADD CONSTRAINT variants_pkey PRIMARY KEY (id);


--
-- Name: variants variants_route_variant_key; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.variants
    ADD CONSTRAINT variants_route_variant_key UNIQUE (route_id, variant_num);


--
-- Name: weather_temperature_clusters weather_temperature_clusters_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.weather_temperature_clusters
    ADD CONSTRAINT weather_temperature_clusters_pkey PRIMARY KEY (id);


--
-- Name: weather_temperature_clusters uq_weather_temp_clusters_config_cluster; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.weather_temperature_clusters
    ADD CONSTRAINT uq_weather_temp_clusters_config_cluster UNIQUE (latitude, longitude, k, start_time, end_time, cluster_id);


--
-- Name: weather_measurements weather_measurements_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.weather_measurements
    ADD CONSTRAINT weather_measurements_pkey PRIMARY KEY (id);


--
-- Name: yearly_analysis yearly_analysis_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.yearly_analysis
    ADD CONSTRAINT yearly_analysis_pkey PRIMARY KEY (id);


--
-- Name: buses_manufacturers buses_manufacturers_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_manufacturers
    ADD CONSTRAINT buses_manufacturers_pkey PRIMARY KEY (id);


--
-- Name: buses_models_refs buses_models_refs_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_models_refs
    ADD CONSTRAINT buses_models_refs_pkey PRIMARY KEY (id);


--
-- Name: buses_lca_data buses_lca_data_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_lca_data
    ADD CONSTRAINT buses_lca_data_pkey PRIMARY KEY (id);


--
-- Name: depots_agency_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX depots_agency_id_idx ON public.depots USING btree (user_id);


--
-- Name: gtfs_stops_times_trip_seq_udx; Type: INDEX; Schema: public; Owner: admin
--

CREATE UNIQUE INDEX gtfs_stops_times_trip_seq_udx ON public.gtfs_stops_times USING btree (trip_id, stop_sequence);


--
-- Name: gtfs_trips_trip_id_udx; Type: INDEX; Schema: public; Owner: admin
--

CREATE UNIQUE INDEX gtfs_trips_trip_id_udx ON public.gtfs_trips USING btree (trip_id);


--
-- Name: idx_buses_bus_model_id; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_buses_bus_model_id ON public.buses USING btree (bus_model_id);


--
-- Name: idx_shifts_bus_id; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX idx_shifts_bus_id ON public.shifts USING btree (bus_id);


--
-- Name: ix_weather_temp_clusters_config; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX ix_weather_temp_clusters_config ON public.weather_temperature_clusters USING btree (latitude, longitude, k, start_time, end_time);


--
-- Name: ix_weather_lat_lon; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX ix_weather_lat_lon ON public.weather_measurements USING btree (latitude, longitude);


--
-- Name: ix_weather_time_brin; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX ix_weather_time_brin ON public.weather_measurements USING brin (time_utc);


--
-- Name: shifts_name_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX shifts_name_idx ON public.shifts USING btree (name);


--
-- Name: shifts_structures_seq_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX shifts_structures_seq_idx ON public.shifts_structures USING btree (sequence_number);


--
-- Name: shifts_structures_shift_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX shifts_structures_shift_idx ON public.shifts_structures USING btree (shift_id);


--
-- Name: shifts_structures_trip_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX shifts_structures_trip_idx ON public.shifts_structures USING btree (trip_id);


--
-- Name: prediction_runs_shift_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX prediction_runs_shift_id_idx ON public.prediction_runs USING btree (shift_id);


--
-- Name: prediction_runs_bus_model_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX prediction_runs_bus_model_id_idx ON public.prediction_runs USING btree (bus_model_id);


--
-- Name: prediction_runs_yearly_analysis_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX prediction_runs_yearly_analysis_id_idx ON public.prediction_runs USING btree (yearly_analysis_id);


--
-- Name: optimization_runs_user_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX optimization_runs_user_id_idx ON public.optimization_runs USING btree (user_id);


--
-- Name: optimization_runs_bus_model_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX optimization_runs_bus_model_id_idx ON public.optimization_runs USING btree (bus_model_id);


--
-- Name: trip_predictions_run_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX trip_predictions_run_id_idx ON public.trip_predictions USING btree (prediction_run_id);


--
-- Name: variants_route_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX variants_route_id_idx ON public.variants USING btree (route_id);


--
-- Name: yearly_analysis_optimization_run_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX yearly_analysis_optimization_run_id_idx ON public.yearly_analysis USING btree (optimization_run_id);


--
-- Name: buses buses_bus_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses
    ADD CONSTRAINT buses_bus_model_id_fkey FOREIGN KEY (bus_model_id) REFERENCES public.buses_models(id) ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: buses_models buses_models_users_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_models
    ADD CONSTRAINT buses_models_users_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: buses buses_users_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses
    ADD CONSTRAINT buses_users_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: depots depots_gtfs_stops_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.depots
    ADD CONSTRAINT depots_gtfs_stops_fk FOREIGN KEY (stop_id) REFERENCES public.gtfs_stops(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: depots depots_users_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.depots
    ADD CONSTRAINT depots_users_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: gtfs_routes gtfs_routes_agency_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_routes
    ADD CONSTRAINT gtfs_routes_agency_id_fkey FOREIGN KEY (agency_id) REFERENCES public.gtfs_agencies(id);


--
-- Name: gtfs_stops_times gtfs_stops_times_stop_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_stops_times
    ADD CONSTRAINT gtfs_stops_times_stop_id_fkey FOREIGN KEY (stop_id) REFERENCES public.gtfs_stops(id);


--
-- Name: gtfs_stops_times gtfs_stops_times_trip_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_stops_times
    ADD CONSTRAINT gtfs_stops_times_trip_id_fkey FOREIGN KEY (trip_id) REFERENCES public.gtfs_trips(id);


--
-- Name: gtfs_trips gtfs_trips_route_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_trips
    ADD CONSTRAINT gtfs_trips_route_id_fkey FOREIGN KEY (route_id) REFERENCES public.gtfs_routes(id);


--
-- Name: gtfs_trips gtfs_trips_service_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.gtfs_trips
    ADD CONSTRAINT gtfs_trips_service_fk FOREIGN KEY (service_id) REFERENCES public.gtfs_calendar(id);


--
-- Name: shifts shifts_bus_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.shifts
    ADD CONSTRAINT shifts_bus_id_fkey FOREIGN KEY (bus_id) REFERENCES public.buses(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: shifts_structures shifts_structures_shift_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.shifts_structures
    ADD CONSTRAINT shifts_structures_shift_fk FOREIGN KEY (shift_id) REFERENCES public.shifts(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: shifts_structures shifts_structures_trip_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.shifts_structures
    ADD CONSTRAINT shifts_structures_trip_fk FOREIGN KEY (trip_id) REFERENCES public.gtfs_trips(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: prediction_runs prediction_runs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.prediction_runs
    ADD CONSTRAINT prediction_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: prediction_runs prediction_runs_shift_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.prediction_runs
    ADD CONSTRAINT prediction_runs_shift_id_fkey FOREIGN KEY (shift_id) REFERENCES public.shifts(id) ON DELETE CASCADE;


--
-- Name: prediction_runs prediction_runs_bus_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.prediction_runs
    ADD CONSTRAINT prediction_runs_bus_model_id_fkey FOREIGN KEY (bus_model_id) REFERENCES public.buses_models(id) ON DELETE RESTRICT;


--
-- Name: prediction_runs prediction_runs_yearly_analysis_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.prediction_runs
    ADD CONSTRAINT prediction_runs_yearly_analysis_id_fkey FOREIGN KEY (yearly_analysis_id) REFERENCES public.yearly_analysis(id) ON DELETE SET NULL;


--
-- Name: optimization_runs optimization_runs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.optimization_runs
    ADD CONSTRAINT optimization_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: optimization_runs optimization_runs_bus_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.optimization_runs
    ADD CONSTRAINT optimization_runs_bus_model_id_fkey FOREIGN KEY (bus_model_id) REFERENCES public.buses_models(id) ON DELETE RESTRICT;


--
-- Name: trip_predictions trip_predictions_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.trip_predictions
    ADD CONSTRAINT trip_predictions_run_id_fkey FOREIGN KEY (prediction_run_id) REFERENCES public.prediction_runs(id) ON DELETE CASCADE;


--
-- Name: trip_predictions trip_predictions_trip_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.trip_predictions
    ADD CONSTRAINT trip_predictions_trip_id_fkey FOREIGN KEY (trip_id) REFERENCES public.gtfs_trips(id) ON DELETE CASCADE;


--
-- Name: users users_gtfs_agencies_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_gtfs_agencies_id_fkey FOREIGN KEY (company_id) REFERENCES public.gtfs_agencies(id) ON DELETE CASCADE;


--
-- Name: variants variants_gtfs_routes_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.variants
    ADD CONSTRAINT variants_gtfs_routes_id_fkey FOREIGN KEY (route_id) REFERENCES public.gtfs_routes(id) ON DELETE CASCADE;


--
-- Name: yearly_analysis yearly_analysis_optimization_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.yearly_analysis
    ADD CONSTRAINT yearly_analysis_optimization_run_id_fkey FOREIGN KEY (optimization_run_id) REFERENCES public.optimization_runs(id) ON DELETE SET NULL;


--
-- Name: buses_models_refs buses_models_refs_manufacturer_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_models_refs
    ADD CONSTRAINT buses_models_refs_manufacturer_fk FOREIGN KEY (buses_manufacturer_id) REFERENCES public.buses_manufacturers(id);


--
-- PostgreSQL database dump complete
--

\unrestrict bSUlLl6GHjK5gJeZ0c9qtfbC1IIhmMf26YTxN0VOt1wbVzVyJgGPF5fYGWbPrxx

