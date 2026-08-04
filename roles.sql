-- Roles for atlas_db access.
-- Run this after atlas_db and schema objects are created.

DO $$
BEGIN
	IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bot_user') THEN
		CREATE ROLE bot_user LOGIN PASSWORD 'admin';
	ELSE
		ALTER ROLE bot_user WITH LOGIN PASSWORD 'admin';
	END IF;

	IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'poll_user') THEN
		CREATE ROLE poll_user LOGIN PASSWORD 'admin';
	ELSE
		ALTER ROLE poll_user WITH LOGIN PASSWORD 'admin';
	END IF;
END
$$;

ALTER DATABASE atlas_db OWNER TO poll_user;
GRANT ALL PRIVILEGES ON DATABASE atlas_db TO poll_user;
GRANT CONNECT ON DATABASE atlas_db TO bot_user;

\connect atlas_db

ALTER SCHEMA public OWNER TO poll_user;

DO $$
DECLARE r record;
BEGIN
	FOR r IN SELECT tablename FROM pg_tables WHERE schemaname = 'public' LOOP
		EXECUTE format('ALTER TABLE public.%I OWNER TO poll_user', r.tablename);
	END LOOP;

	FOR r IN SELECT sequencename FROM pg_sequences WHERE schemaname = 'public' LOOP
		EXECUTE format('ALTER SEQUENCE public.%I OWNER TO poll_user', r.sequencename);
	END LOOP;

	FOR r IN
		SELECT p.oid::regprocedure AS procname
		FROM pg_proc p
		JOIN pg_namespace n ON n.oid = p.pronamespace
		WHERE n.nspname = 'public'
	LOOP
		EXECUTE format('ALTER ROUTINE %s OWNER TO poll_user', r.procname);
	END LOOP;
END
$$;

GRANT ALL PRIVILEGES ON SCHEMA public TO poll_user;
GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA public TO poll_user;
GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public TO poll_user;

REVOKE ALL PRIVILEGES ON SCHEMA public FROM bot_user;
GRANT USAGE ON SCHEMA public TO bot_user;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM bot_user;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO bot_user;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM bot_user;
GRANT SELECT ON ALL SEQUENCES IN SCHEMA public TO bot_user;

ALTER DEFAULT PRIVILEGES FOR ROLE poll_user IN SCHEMA public
	REVOKE ALL ON TABLES FROM bot_user;
ALTER DEFAULT PRIVILEGES FOR ROLE poll_user IN SCHEMA public
	GRANT SELECT ON TABLES TO bot_user;
ALTER DEFAULT PRIVILEGES FOR ROLE poll_user IN SCHEMA public
	REVOKE ALL ON SEQUENCES FROM bot_user;
ALTER DEFAULT PRIVILEGES FOR ROLE poll_user IN SCHEMA public
	GRANT SELECT ON SEQUENCES TO bot_user;
