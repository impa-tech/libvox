-- Runs once, on first initialization of the volume. Roles before databases,
-- because 02 grants to them.
--
-- Why this file exists: POSTGRES_USER creates a *superuser*, and superusers
-- bypass row-level security unconditionally. If an application connected as
-- that role, every tenant isolation policy in every one of these databases
-- would be inert -- present in the schema, enforcing nothing. So the
-- applications get a role that cannot do that.
--
-- Three roles, three jobs:
--   libvox           (superuser, from POSTGRES_USER) -- owns the databases,
--                                                       runs the migrations
--   libvox_app       (unprivileged)                  -- reads and writes
--   libvox_resolver  (cannot log in)                 -- owns one function
--
-- **One `libvox_app` for all six databases, and that is a decision.** It used
-- to be two roles that merely shared a name, because they lived in two
-- separate clusters; the separation was physical and free. Now it is one role
-- and the separation is grants. Three reasons that is the right trade:
--
--   * It was already the declared design. The file this one replaces said the
--     name matched /libvox-sync's "on purpose -- one name for one privilege
--     level is what keeps the grants readable across three components."
--   * The physical separation was already illusory in development: both
--     clusters created the same role with the same password.
--   * Row-level security was always the real boundary. What keeps library A
--     out of library B's rows is `tenant_isolation` with FORCE, not the number
--     of servers -- and that is unchanged here.
--
-- What 02 adds in exchange is an explicit REVOKE, so that reaching a database
-- is a grant somebody wrote rather than a PostgreSQL default.
--
-- The password below is a local development convenience. In any real
-- deployment these roles are created by your provisioning, with secrets from
-- your secrets manager -- never from a file in a repository.

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        CREATE ROLE libvox_app LOGIN PASSWORD 'libvox_app'
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;

-- A role that cannot log in, and that is the whole point of it.
--
-- Resolving "which library owns this widget key" happens before any tenant is
-- known, so it cannot run inside a tenant scope and row-level security would
-- match nothing. It is the one genuinely cross-tenant read in the system.
--
-- It is answered by a SECURITY DEFINER function owned by this role. SECURITY
-- DEFINER runs as the function's owner, and FORCE ROW LEVEL SECURITY subjects
-- an owner to its own policies -- so the owner needs a permissive policy, and
-- giving one to a role that already serves requests would be a hole the width
-- of every table it can reach.
--
-- Hence NOLOGIN: nobody can connect as this role. The only way to exercise its
-- privileges is to call the function, and the width of the hole is therefore
-- the function's return type, which is one column and never a secret.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_resolver') THEN
        CREATE ROLE libvox_resolver NOLOGIN
            NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
END
$$;
