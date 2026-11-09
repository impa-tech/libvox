<?php

namespace App\Channels;

/**
 * The public identifier a library embeds in its OPAC page.
 *
 * Not a credential. It says which library, and it authorizes nothing -- what
 * authorizes a connection is the Origin allowlist beside it. It is long and
 * random anyway, because guessing one is the first half of impersonating a
 * library's widget, and there is no reason to make that half free.
 *
 * random_bytes rather than Str::random, so the CSPRNG is visible at the point
 * of use rather than a property of whatever Str::random happens to call.
 */
final class WidgetKey
{
    /** Recognizable in a log line or a support ticket, and greppable. */
    private const PREFIX = 'wk_';

    public static function mint(): string
    {
        return self::PREFIX.rtrim(strtr(base64_encode(random_bytes(24)), '+/', '-_'), '=');
    }

    public static function looksLikeOne(string $candidate): bool
    {
        return (bool) preg_match('/^wk_[A-Za-z0-9_-]{32,}$/', $candidate);
    }
}
