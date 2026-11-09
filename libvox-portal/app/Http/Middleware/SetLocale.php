<?php

namespace App\Http\Middleware;

use App\Support\Locale;
use Closure;
use Filament\Facades\Filament;
use Illuminate\Http\Request;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\App;
use Symfony\Component\HttpFoundation\Response;

/**
 * Which language a panel speaks, decided per request.
 *
 * In order: the person's own preference, then their library's default, then
 * what the browser asks for, then the configured default. The person wins
 * because a librarian who set their account to English meant it, even at a
 * library whose readers are answered in Portuguese -- the two are different
 * audiences and the panel is not the widget.
 */
class SetLocale
{
    public function handle(Request $request, Closure $next): Response
    {
        $user = Filament::auth()->user();

        $tag = collect([
            $user?->locale,
            $user?->tenant?->locale,
            $request->getPreferredLanguage(Locale::SUPPORTED),
            config('app.locale'),
        ])->map(fn (?string $candidate) => $candidate === null ? null : Locale::toTag($candidate))
            ->first(Locale::isSupported(...)) ?? Locale::FALLBACK;

        App::setLocale(Locale::toLaravel($tag));
        Carbon::setLocale(Locale::toLaravel($tag));

        return $next($request);
    }
}
