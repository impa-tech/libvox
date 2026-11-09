<?php

namespace App\Ils;

use App\Enums\IlsType;

/**
 * Everything about a library's ILS connection except the secret.
 *
 * The secret is not a property here and never becomes one: it travels as a
 * bare parameter to IlsCredentialStore::put and stops there. A DTO that could
 * hold it is a DTO that can be dumped, serialized into a Livewire snapshot,
 * logged by an exception handler, or returned by an accessor someone adds next
 * year.
 */
final readonly class IlsConnection
{
    public function __construct(
        public string $tenantId,
        public IlsType $ilsType,
        public string $baseUrl,
        public string $clientId,
    ) {}
}
