<?php

namespace App\Ils;

use App\Database\TenantScope;
use App\Enums\IlsType;
use Illuminate\Database\ConnectionInterface;
use Illuminate\Database\QueryException;
use SensitiveParameter;
use Throwable;

/**
 * The only way an ILS secret enters LibVox -- and there is no way to get one
 * back out.
 *
 * That asymmetry is the design, not an omission. The root README says
 * /libvox-core is the only component that decrypts a library's credentials;
 * making that true here means this class has no read path at all. There is no
 * `reveal()`, no `getSecret()`, no accessor on the model, and a test greps the
 * application for pgp_sym_decrypt to keep it that way.
 *
 * What a portal legitimately needs is `describe()`: which ILS, at which URL,
 * under which client_id, last changed when. None of that is the secret.
 *
 * "Test this connection" is offered at save time, against the secret the
 * librarian has just typed, still in memory, before it is written. Not against
 * a stored one -- that would require the read path this class refuses to have.
 */
class IlsCredentialStore
{
    public function __construct(
        private readonly ConnectionInterface $connection,
        private readonly SecretCipher $cipher,
        private readonly TenantScope $scope,
    ) {}

    /**
     * Store or replace one library's ILS credentials.
     *
     * @throws CredentialStorageFailed
     */
    public function put(
        IlsConnection $connection,
        #[SensitiveParameter] string $clientSecret,
    ): void {
        [$expression, $cryptoBindings] = $this->cipher->encryptExpression($clientSecret);

        $sql = <<<SQL
            INSERT INTO tenant_ils
                   (tenant_id, ils_type, base_url, client_id, client_secret_enc)
            VALUES (?, ?, ?, ?, {$expression})
            ON CONFLICT (tenant_id) DO UPDATE
               SET ils_type          = EXCLUDED.ils_type,
                   base_url          = EXCLUDED.base_url,
                   client_id         = EXCLUDED.client_id,
                   client_secret_enc = EXCLUDED.client_secret_enc,
                   updated_at        = now()
            SQL;

        try {
            $this->scope->run($connection->tenantId, function () use ($sql, $connection, $cryptoBindings) {
                $this->connection->statement($sql, [
                    $connection->tenantId,
                    $connection->ilsType->value,
                    $connection->baseUrl,
                    $connection->clientId,
                    ...$cryptoBindings,
                ]);

                // Same transaction as the credential, so the operator's view of
                // "is this library configured" cannot drift from the table it
                // describes. tenants has no row-level security -- it is the
                // registry that defines the tenants -- which is exactly why this
                // is the column the operator can read across libraries.
                $this->connection->update(
                    'UPDATE tenants SET ils_configured_at = now() WHERE tenant_id = ?',
                    [$connection->tenantId],
                );
            });
        } catch (QueryException) {
            // Not rethrown, not chained. See CredentialStorageFailed: the
            // message of a QueryException on this statement contains the
            // plaintext secret and the encryption key.
            throw new CredentialStorageFailed(
                "could not store ILS credentials for {$connection->tenantId}"
            );
        } catch (Throwable $other) {
            // Anything else is safe to surface: only the query carries the
            // bindings.
            throw $other;
        }
    }

    /**
     * Change where the catalog is, leaving the stored secret untouched.
     *
     * A separate method rather than a nullable argument on put(), because the
     * two are different statements and a caller must choose deliberately. The
     * nullable version reads as harmless and writes an empty secret over a
     * working one the first time somebody submits a form without retyping it --
     * which is every time, since the field is write-only and renders blank.
     *
     * Only ever an update: client_secret_enc is NOT NULL, so there is no row
     * this could create.
     *
     * @throws CredentialStorageFailed
     */
    public function updateConnection(IlsConnection $connection): void
    {
        $sql = <<<'SQL'
            UPDATE tenant_ils
               SET ils_type   = ?,
                   base_url   = ?,
                   client_id  = ?,
                   updated_at = now()
             WHERE tenant_id = ?
            SQL;

        $changed = $this->scope->run($connection->tenantId, fn () => $this->connection->update($sql, [
            $connection->ilsType->value,
            $connection->baseUrl,
            $connection->clientId,
            $connection->tenantId,
        ]));

        if ($changed === 0) {
            throw new CredentialStorageFailed(
                "no ILS connection stored for {$connection->tenantId} to update"
            );
        }
    }

    /**
     * Everything about the stored connection except the secret.
     *
     * The column is not in the SELECT list, so this cannot return ciphertext
     * even by accident.
     */
    public function describe(string $tenantId): ?IlsConnectionSummary
    {
        $row = $this->scope->run($tenantId, fn () => $this->connection->selectOne(
            'SELECT ils_type, base_url, client_id, updated_at FROM tenant_ils WHERE tenant_id = ?',
            [$tenantId],
        ));

        if ($row === null) {
            return null;
        }

        return new IlsConnectionSummary(
            connection: new IlsConnection(
                tenantId: $tenantId,
                ilsType: IlsType::from($row->ils_type),
                baseUrl: $row->base_url,
                clientId: $row->client_id,
            ),
            secretUpdatedAt: $row->updated_at,
        );
    }

    public function forget(string $tenantId): void
    {
        $this->scope->run($tenantId, function () use ($tenantId) {
            $this->connection->delete('DELETE FROM tenant_ils WHERE tenant_id = ?', [$tenantId]);

            $this->connection->update(
                'UPDATE tenants SET ils_configured_at = NULL WHERE tenant_id = ?',
                [$tenantId],
            );
        });
    }
}
