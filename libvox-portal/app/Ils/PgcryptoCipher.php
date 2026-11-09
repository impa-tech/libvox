<?php

namespace App\Ils;

use SensitiveParameter;

/**
 * pgcrypto, byte-for-byte what libvox_sync.credentials already writes and
 * reads.
 *
 * That compatibility is the point. The portal writes the credential and
 * /libvox-sync reads it, in a different language, and the format they agree on
 * is the one PostgreSQL itself defines. There is no envelope to specify, no
 * version byte to negotiate, and no second implementation to keep in step --
 * the interoperability test in libvox-sync passes because both sides call the
 * same function.
 *
 * The root README's rule is "never Laravel's Crypt facade", and it is easy to
 * misread as "never encrypt in the database". Crypt is keyed by APP_KEY and
 * wraps AES-256-CBC in a Laravel-specific base64 JSON envelope that Python
 * would have to reimplement to read one column. pgcrypto is the opposite: it
 * is already language-neutral, and it is already the format the one existing
 * consumer reads.
 *
 * What it costs, said plainly: the key travels to the server as a bind
 * parameter on every statement, so anything logging query bindings logs
 * LIBVOX_CRYPTO_KEY beside the ciphertext it protects. libvox_sync's own
 * module docstring acknowledges the same exposure. It is why this application
 * catches QueryException before it can be logged, and why no query inspector
 * may be installed here.
 */
final readonly class PgcryptoCipher implements SecretCipher
{
    /**
     * AES-256, no compression. These values are short, and compressing a
     * secret leaks information about its content through its length.
     */
    private const OPTIONS = 'compress-algo=0, cipher-algo=aes256';

    public function __construct(#[SensitiveParameter] private string $key) {}

    public function encryptExpression(#[SensitiveParameter] string $plaintext): array
    {
        // The key is a bind parameter, never a session variable. A session
        // setting persists for the life of the transaction and is readable
        // through current_setting; a parameter is used for one statement and
        // is gone. Neither is invisible to a server with verbose statement
        // logging, but the parameter leaves the smaller footprint. Same
        // reasoning, same words, as libvox_sync/credentials.py.
        return ['pgp_sym_encrypt(?, ?, ?)', [$plaintext, $this->key, self::OPTIONS]];
    }
}
