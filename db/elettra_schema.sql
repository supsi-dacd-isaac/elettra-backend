--
-- PostgreSQL database dump
--

\restrict 12vqELAoXgtmQzLhYR0ufbJAHHiqOYFRFkXVE1a2NTKS8VIgkICBbvNHAIZ66W6

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
    continuous_drop_off integer
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
-- Name: simulation_runs; Type: TABLE; Schema: public; Owner: admin
--

CREATE TABLE public.simulation_runs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id uuid NOT NULL,
    input_params jsonb NOT NULL,
    optimal_battery_kwh numeric,
    output_results jsonb,
    status public.sim_status DEFAULT 'pending'::public.sim_status NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    completed_at timestamp with time zone,
    variant_id uuid NOT NULL
);


ALTER TABLE public.simulation_runs OWNER TO admin;

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
-- Name: buses buses_company_id_name_key; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses
    ADD CONSTRAINT buses_company_id_name_key UNIQUE (user_id, name);


--
-- Name: buses_models buses_models_company_id_name_key; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_models
    ADD CONSTRAINT buses_models_company_id_name_key UNIQUE (name);


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
-- Name: simulation_runs simulation_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.simulation_runs
    ADD CONSTRAINT simulation_runs_pkey PRIMARY KEY (id);


--
-- Name: weather_measurements uq_weather_time_lat_lon; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.weather_measurements
    ADD CONSTRAINT uq_weather_time_lat_lon UNIQUE (time_utc, latitude, longitude);


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
-- Name: weather_measurements weather_measurements_pkey; Type: CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.weather_measurements
    ADD CONSTRAINT weather_measurements_pkey PRIMARY KEY (id);


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
-- Name: simulation_runs_variant_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX simulation_runs_variant_id_idx ON public.simulation_runs USING btree (variant_id);


--
-- Name: variants_route_id_idx; Type: INDEX; Schema: public; Owner: admin
--

CREATE INDEX variants_route_id_idx ON public.variants USING btree (route_id);


--
-- Name: buses buses_bus_model_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses
    ADD CONSTRAINT buses_bus_model_id_fkey FOREIGN KEY (bus_model_id) REFERENCES public.buses_models(id) ON UPDATE CASCADE ON DELETE RESTRICT;


--
-- Name: buses_models buses_models_users_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses_models
    ADD CONSTRAINT buses_models_users_fk FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: buses buses_users_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.buses
    ADD CONSTRAINT buses_users_fk FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: depots depots_gtfs_stops_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.depots
    ADD CONSTRAINT depots_gtfs_stops_fk FOREIGN KEY (stop_id) REFERENCES public.gtfs_stops(id) ON UPDATE CASCADE ON DELETE CASCADE;


--
-- Name: depots depots_users_fk; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.depots
    ADD CONSTRAINT depots_users_fk FOREIGN KEY (user_id) REFERENCES public.users(id);


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
-- Name: simulation_runs simulation_runs_user_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.simulation_runs
    ADD CONSTRAINT simulation_runs_user_id_fkey FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: simulation_runs simulation_runs_variant_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: admin
--

ALTER TABLE ONLY public.simulation_runs
    ADD CONSTRAINT simulation_runs_variant_id_fkey FOREIGN KEY (variant_id) REFERENCES public.variants(id) ON DELETE CASCADE;


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
-- PostgreSQL database dump complete
--

\unrestrict 12vqELAoXgtmQzLhYR0ufbJAHHiqOYFRFkXVE1a2NTKS8VIgkICBbvNHAIZ66W6

