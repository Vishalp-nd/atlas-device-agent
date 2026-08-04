--
-- PostgreSQL database dump
--

\restrict BQRT1a4rqDH9WFr8o9LHorUR9D81iePYdZYVKDPzWGRfNQMfp0EWK2h7CITdhYT

-- Dumped from database version 14.23 (Ubuntu 14.23-0ubuntu0.22.04.1)
-- Dumped by pg_dump version 14.23 (Ubuntu 14.23-0ubuntu0.22.04.1)

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

SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: critical_info_priority; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.critical_info_priority (
    "CODE" double precision,
    "CODE_AUX" bigint,
    "DESCRIPTION" text,
    "VERSION" text,
    "TRIGGER_REASON" text,
    "PRIORITY" text
);


--
-- Name: criticalinfo_snowflakes_data; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.criticalinfo_snowflakes_data (
    "DEVICE_ID" text,
    "TIMESTAMP" timestamp without time zone,
    "PROCESS_NAME" text,
    "CODE" double precision,
    "CODE_AUX" bigint,
    "COUNT" bigint,
    "DESCRIPTION" text,
    "DEVICE_VERSION" text,
    "SYS_UPTIME" double precision,
    "S3_PATH" text,
    "TENANT_ID" bigint,
    "UPSERT_TIME" timestamp without time zone,
    "LOADED_TO_SNOWFLAKE_ON" timestamp without time zone,
    type text
);


--
-- Name: unique_critical_info; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.unique_critical_info (
    "CODE" double precision,
    "CODE_AUX" bigint,
    "TYPE" text,
    description_pattern text,
    sample_description text
);


--
-- Name: criticalinfo_snowflakes_data criticalinfo_snowflakes_data_uniq_device_ts_proc_code_desc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.criticalinfo_snowflakes_data
    ADD CONSTRAINT criticalinfo_snowflakes_data_uniq_device_ts_proc_code_desc UNIQUE ("DEVICE_ID", "TIMESTAMP", "PROCESS_NAME", "CODE", "DESCRIPTION");


--
-- Name: idx_ce_code; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_code ON public.criticalinfo_snowflakes_data USING btree ("CODE");


--
-- Name: idx_ce_proc; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_proc ON public.criticalinfo_snowflakes_data USING btree ("PROCESS_NAME");


--
-- Name: idx_ce_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_ts ON public.criticalinfo_snowflakes_data USING btree ("TIMESTAMP");


--
-- Name: idx_ce_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_ce_type ON public.criticalinfo_snowflakes_data USING btree (type);


--
-- PostgreSQL database dump complete
--

\unrestrict BQRT1a4rqDH9WFr8o9LHorUR9D81iePYdZYVKDPzWGRfNQMfp0EWK2h7CITdhYT
