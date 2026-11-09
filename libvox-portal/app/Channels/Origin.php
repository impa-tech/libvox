<?php

namespace App\Channels;

/**
 * An origin, normalized once so it can be compared as a string forever.
 *
 * The Origin header is matched exactly, and "almost equal" is "not equal":
 * https://Biblioteca.FURG.br:443/ and https://biblioteca.furg.br are the same
 * origin and must not be two rows in an allowlist.
 *
 * Wildcards are not supported and are listed under "not implemented yet"
 * rather than approximated. A library asking for *.furg.br is asking a real
 * question, and answering it with a LIKE would turn an allowlist into
 * something that quietly permits more than it says.
 */
final class Origin
{
    /** Scheme and host, plus a port only when it is not the default. */
    public static function normalize(string $raw): ?string
    {
        $parts = parse_url(trim($raw));

        if ($parts === false || ! isset($parts['scheme'], $parts['host'])) {
            return null;
        }

        $scheme = strtolower($parts['scheme']);

        if (! in_array($scheme, ['http', 'https'], true)) {
            return null;
        }

        $host = strtolower($parts['host']);

        // parse_url is permissive about what a host may contain -- it accepts
        // '*.furg.br' happily. Everything after this point treats the host as
        // an exact string to match, so anything that is not one is refused
        // here rather than stored and compared later.
        if (! preg_match('/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)*$/', $host)) {
            return null;
        }
        $port = $parts['port'] ?? null;
        $isDefaultPort = ($scheme === 'http' && $port === 80) || ($scheme === 'https' && $port === 443);

        return $scheme.'://'.$host.($port !== null && ! $isDefaultPort ? ':'.$port : '');
    }
}
