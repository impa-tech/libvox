<?php

namespace App\Support;

/**
 * Two spellings of one language, and the boundary between them.
 *
 * Laravel's lang directories and App::setLocale use pt_BR, with an underscore.
 * The BCP 47 tag that belongs in <html lang>, in tenants.locale, in
 * tenant_widget.default_locale, and in whatever /libvox-core hands the widget
 * is pt-BR, with a hyphen.
 *
 * The database stores the hyphenated tag, because that value crosses the wire
 * to a browser and to another component; the underscore is a detail of one
 * framework's directory layout and stops here. Getting this backwards costs an
 * hour and produces missing translations rather than an error, which is why it
 * is a named class rather than two str_replace calls at the call sites.
 */
final class Locale
{
    public const SUPPORTED = ['pt-BR', 'en'];

    public const FALLBACK = 'en';

    /** BCP 47 to Laravel: pt-BR becomes pt_BR. */
    public static function toLaravel(string $tag): string
    {
        return str_replace('-', '_', $tag);
    }

    /** Laravel to BCP 47: pt_BR becomes pt-BR. */
    public static function toTag(string $laravel): string
    {
        return str_replace('_', '-', $laravel);
    }

    public static function isSupported(?string $tag): bool
    {
        return $tag !== null && in_array($tag, self::SUPPORTED, true);
    }

    /** The other one, for a two-language switcher. */
    public static function other(string $tag): string
    {
        return $tag === 'pt-BR' ? 'en' : 'pt-BR';
    }
}
