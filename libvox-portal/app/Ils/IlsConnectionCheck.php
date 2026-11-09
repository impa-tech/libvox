<?php

namespace App\Ils;

use Illuminate\Support\Facades\Http;
use Illuminate\Support\Facades\Log;
use Throwable;

/**
 * The answer to "does my catalogue connection work?", asked of /libvox-core.
 *
 * **This application cannot answer it.** `IlsCredentialStore` has `put`,
 * `updateConnection`, `describe` and `forget` and no read path at all, and two
 * tests hold that line -- one of them tokenizes every PHP file under `app/`
 * looking for `pgp_sym_decrypt`. Testing a stored credential means decrypting
 * it, so the question goes to the component that may.
 *
 * The tenant is not a parameter a caller chooses: it comes from the signed-in
 * user, at the one call site. Same rule as everywhere else here.
 */
class IlsConnectionCheck
{
    public function __construct(
        private readonly string $coreUrl,
        private readonly ?string $serviceToken,
    ) {}

    /**
     * @return array{ok: bool, problem: ?string, detail: ?string, records_seen: int}
     */
    public function run(string $tenantId): array
    {
        try {
            $response = Http::timeout(45)
                ->withHeaders(array_filter([
                    'x-libvox-service-token' => $this->serviceToken,
                ]))
                ->post($this->coreUrl.'/internal/v1/ils/check', [
                    'tenant_id' => $tenantId,
                ]);
        } catch (Throwable $exception) {
            // LibVox being unreachable is a different problem from the
            // library's Koha being unreachable, and saying so is the whole
            // point -- otherwise a librarian goes and checks a server that was
            // fine all along.
            Log::warning('ILS check could not reach /libvox-core', [
                'tenant_id' => $tenantId,
                'error' => $exception->getMessage(),
            ]);

            return self::unreachable();
        }

        if (! $response->successful()) {
            Log::warning('ILS check refused by /libvox-core', [
                'tenant_id' => $tenantId,
                'status' => $response->status(),
            ]);

            return self::unreachable();
        }

        $body = $response->json();

        return [
            'ok' => (bool) ($body['ok'] ?? false),
            'problem' => $body['problem'] ?? null,
            'detail' => $body['detail'] ?? null,
            'records_seen' => (int) ($body['records_seen'] ?? 0),
        ];
    }

    /** @return array{ok: bool, problem: string, detail: null, records_seen: int} */
    private static function unreachable(): array
    {
        return ['ok' => false, 'problem' => 'core_unreachable', 'detail' => null, 'records_seen' => 0];
    }
}
