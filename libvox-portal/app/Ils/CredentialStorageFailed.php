<?php

namespace App\Ils;

use RuntimeException;

/**
 * A credential could not be stored.
 *
 * Deliberately carries no detail and no previous exception. Laravel's
 * QueryException builds its message by interpolating the bindings into the
 * SQL, and the bindings on a credential statement are the plaintext secret and
 * LIBVOX_CRYPTO_KEY. Letting one propagate writes both into
 * storage/logs/laravel.log, into any error tracker, and onto a debug page --
 * next to the ciphertext the key protects.
 *
 * So the original is dropped rather than chained. The operator loses the
 * database's explanation, which is a real cost; the alternative is a log file
 * that is worth more than the database it describes.
 */
class CredentialStorageFailed extends RuntimeException {}
