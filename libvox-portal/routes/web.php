<?php

use App\Support\Locale;
use Illuminate\Http\Request;
use Illuminate\Support\Facades\Auth;
use Illuminate\Support\Facades\Route;

/*
 * The public face: one static page at the root, and the two Filament panels
 * under /app and /admin. There is still no API here -- everything a reader
 * touches lives in /libvox-core, and everything a machine reads comes from the
 * database.
 *
 * This used to redirect to /app, which meant anyone who heard the project's
 * name and typed the domain landed on a sign-in form they could not use.
 *
 * Served as a raw file rather than a Blade view, and that is deliberate: the
 * page carries its own CSS and JavaScript, and `{{` is Blade syntax. One double
 * brace inside a script would break the render in production, in a file nobody
 * thinks to test. response()->file() never parses it.
 */
Route::get('/', fn () => response()->file(public_path('site.html')));

/*
 * The language switcher.
 *
 * Stored on the account rather than in the session, so the choice survives
 * signing in from somewhere else -- and so the switcher is a preference rather
 * than a per-tab accident.
 */
Route::post('/locale/{tag}', function (Request $request, string $tag) {
    abort_unless(Locale::isSupported($tag), 404);

    // Filament's guards are per panel; whichever one is signed in is the one
    // whose preference this is.
    $user = Auth::user();
    abort_unless($user !== null, 403);

    $user->update(['locale' => $tag]);

    return back();
})->middleware('web')->name('locale.switch');
