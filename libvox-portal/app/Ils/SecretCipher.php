<?php

namespace App\Ils;

use SensitiveParameter;

/**
 * How a secret becomes ciphertext on its way into the database.
 *
 * Not a cipher that returns a string: pgcrypto encrypts *inside* PostgreSQL,
 * so the operation is a SQL expression and its bindings, not a value this
 * process computes. Modelling it as `encrypt(string): string` would have made
 * the seam impossible to put here, which is the whole reason there is a seam.
 *
 * There is exactly one implementation today. It exists as an interface so that
 * moving to an application-level AEAD later -- an explicit versioned
 * AES-256-GCM record, or libsodium -- is one new class and a binding, rather
 * than a rewrite of everything that stores a credential.
 */
interface SecretCipher
{
    /**
     * A SQL expression that encrypts $plaintext, and the bindings it needs.
     *
     * @return array{0: string, 1: array<int, string>}
     */
    public function encryptExpression(#[SensitiveParameter] string $plaintext): array;
}
