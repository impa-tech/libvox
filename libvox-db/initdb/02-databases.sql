-- Six databases: three that hold data, and a `_test` copy of each.
--
-- **One database per writer.** That is the rule, and it is why these are six
-- databases on one server rather than one database with six schemas. Each has
-- exactly one component that authors its schema and writes to it; everyone
-- else reads.
--
--   libvox_tenant         written by /libvox-portal -- the registry, the
--                         encrypted ILS credentials, the channel bindings.
--                         /libvox-core and /libvox-sync read it.
--   libvox_conversations  written by /libvox-core -- what readers asked, what
--                         MARC.IA answered, and the thumbs. /libvox-portal
--                         reads it so an operator can judge the answers.
--   libvox_index          written by /libvox-sync -- the Discovery Index, a
--                         derived read model that a full import rebuilds.
--                         /libvox-core reads it to answer search_catalog.
--
-- The index was called `libvox` while it had a server to itself, where the
-- name was unambiguous. Next to libvox_tenant and libvox_conversations it
-- read like the main database rather than one of three, so it says what it is.
--
-- **Why the tests get their own databases rather than transactions.** The
-- suites exercise row-level security, pgcrypto, CHECK constraints, triggers
-- and SECURITY DEFINER functions -- none of which exist under SQLite, so every
-- isolation assertion would pass vacuously against an in-memory database,
-- which is worse than no test at all. And they truncate. A test run must not
-- be able to empty the development data, so the rule in both conftest files is
-- that a test which truncates talks to a `_test` database or refuses to run.
--
-- **REVOKE before GRANT, on every one of them.** PostgreSQL grants CONNECT to
-- PUBLIC by default, which means any role that can log in can reach any
-- database nobody thought to lock. That was survivable when these lived in two
-- clusters and each held one component's data. On one server it is the
-- difference between "libvox_app reaches these six because somebody wrote it
-- down" and "libvox_app reaches everything that will ever be created here".

-- The registry, and the only writer of tenant configuration.
CREATE DATABASE libvox_tenant OWNER libvox;
REVOKE CONNECT ON DATABASE libvox_tenant FROM PUBLIC;
GRANT  CONNECT ON DATABASE libvox_tenant TO libvox_app;

CREATE DATABASE libvox_tenant_test OWNER libvox;
REVOKE CONNECT ON DATABASE libvox_tenant_test FROM PUBLIC;
GRANT  CONNECT ON DATABASE libvox_tenant_test TO libvox_app;

-- The conversation log. Owned and migrated by /libvox-core, which is what
-- `./core.sh migrate` does; the portal never writes here.
CREATE DATABASE libvox_conversations OWNER libvox;
REVOKE CONNECT ON DATABASE libvox_conversations FROM PUBLIC;
GRANT  CONNECT ON DATABASE libvox_conversations TO libvox_app;

CREATE DATABASE libvox_conversations_test OWNER libvox;
REVOKE CONNECT ON DATABASE libvox_conversations_test FROM PUBLIC;
GRANT  CONNECT ON DATABASE libvox_conversations_test TO libvox_app;

-- The Discovery Index. Rebuilt a generation at a time by /libvox-sync.
CREATE DATABASE libvox_index OWNER libvox;
REVOKE CONNECT ON DATABASE libvox_index FROM PUBLIC;
GRANT  CONNECT ON DATABASE libvox_index TO libvox_app;

CREATE DATABASE libvox_index_test OWNER libvox;
REVOKE CONNECT ON DATABASE libvox_index_test FROM PUBLIC;
GRANT  CONNECT ON DATABASE libvox_index_test TO libvox_app;
