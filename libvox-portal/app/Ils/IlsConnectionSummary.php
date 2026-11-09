<?php

namespace App\Ils;

/**
 * What the portal may show about a stored ILS connection.
 *
 * `secretUpdatedAt` is how a librarian tells a configured connection from an
 * unconfigured one without the value being shown. It is the whole of what this
 * application knows about the secret: that there is one, and when it changed.
 */
final readonly class IlsConnectionSummary
{
    public function __construct(
        public IlsConnection $connection,
        public ?string $secretUpdatedAt,
    ) {}
}
