-- 003 — tenant_ils moves to the Tenant DB
--
-- Migration 002 said this table properly belongs to the Tenant DB, owned by
-- the portal, and lived here only because that module did not exist. It does
-- now, and it owns this table through its own migrations. This is the other
-- half of that sentence.
--
-- WHY A NEW FILE RATHER THAN AN EDIT
--
-- Editing an applied migration raises MigrationError: the runner records a
-- checksum and refuses a file that changed after being applied. That rule is
-- worth more than the tidiness of a smaller migrations directory.
--
-- On a fresh database this creates the table (002) and immediately drops it
-- (003). That looks like an oversight and is not: a migration history that
-- silently rewrites itself is a worse failure than a redundant pair of steps.
--
-- ORDERING — THIS DROP IS IRREVERSIBLE
--
-- The ciphertext goes with the table. Move the data first:
--
--   1. /libvox-portal: docker compose up -d postgres && composer migrate
--   2. seed tenants from the tenant_id values already in use
--   3. copy the rows across, as the superuser on both sides
--   4. then run this
--
-- The copy is byte-for-byte. client_secret_enc is pgcrypto ciphertext under
-- LIBVOX_CRYPTO_KEY on both sides, so nothing is decrypted, nothing is
-- re-encrypted, and the key never enters the move. See the README.

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_tables WHERE tablename = 'tenant_ils')
       AND EXISTS (SELECT 1 FROM tenant_ils)
    THEN
        RAISE EXCEPTION
            'tenant_ils still has rows. Copy them to the Tenant DB before '
            'applying 003: this drop destroys the ciphertext, and the '
            'credentials would have to be collected again from every library.';
    END IF;
END
$$;

DROP TABLE IF EXISTS tenant_ils;

-- pgcrypto stays. Nothing else here uses it today, but dropping an extension
-- something may have come to depend on buys nothing and risks a surprise.
