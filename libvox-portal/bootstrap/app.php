<?php

use App\Ils\CredentialStorageFailed;
use Illuminate\Database\QueryException;
use Illuminate\Foundation\Application;
use Illuminate\Foundation\Configuration\Exceptions;
use Illuminate\Foundation\Configuration\Middleware;
use Illuminate\Http\Request;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware): void {
        /*
         * Which proxies may speak for the client, and empty unless something
         * says otherwise -- so a laptop behaves exactly as it did before this
         * existed: nothing is trusted and X-Forwarded-* is ignored.
         *
         * Behind a reverse proxy terminating TLS, the absence of this is not
         * subtle. Laravel sees the hop from the proxy, which is plain HTTP, so
         * every URL it generates says http -- and the redirect after a
         * successful login sends the browser back out of https, on the one
         * request that just carried a password.
         *
         * `TRUSTED_PROXIES=*` is the right value where the panel is reachable
         * *only* through the proxy, which is what binding 8090 to loopback on
         * that host buys: there is exactly one hop, and it is ours.
         */
        $proxies = trim((string) env('TRUSTED_PROXIES', ''));

        if ($proxies !== '') {
            $middleware->trustProxies(
                at: $proxies === '*' ? '*' : array_map(trim(...), explode(',', $proxies)),
            );
        }
    })
    ->withExceptions(function (Exceptions $exceptions): void {
        /*
         * Laravel's default list is password, password_confirmation and
         * current_password. It does not know about client_secret, and without
         * this line one failed validation puts a library's ILS secret into the
         * session and hands it back to the form as old input.
         *
         * This is the easiest thing in the whole module to leave out, and its
         * absence would look exactly like everything working.
         */
        $exceptions->dontFlash([
            'client_secret',
            'client_secret_confirmation',
            'current_password',
            'password',
            'password_confirmation',
        ]);

        /*
         * QueryException builds its message by interpolating the bindings into
         * the SQL. On a credential statement those bindings are the plaintext
         * client secret and LIBVOX_CRYPTO_KEY, so an uncaught one writes both
         * to storage/logs/laravel.log and to any error tracker -- beside the
         * ciphertext the key exists to protect.
         *
         * IlsCredentialStore already catches it at the source. This is the
         * backstop for every query that anyone adds later without thinking
         * about it, which is most of them.
         */
        $exceptions->map(function (QueryException $e) {
            $key = config('libvox.crypto_key');

            // Mapping every QueryException would cost the operator the
            // database's own explanation for every constraint violation in the
            // application -- a real loss, for a hazard that only exists on the
            // statements carrying a secret. So the test is whether the message
            // actually contains the key: precise, cheap, and it still catches a
            // future caller who forgets to wrap their own query.
            if (is_string($key) && $key !== '' && str_contains($e->getMessage(), $key)) {
                return new RuntimeException(
                    'A database error occurred on a statement carrying a secret. '
                    .'Its details are withheld: a query exception message '
                    .'contains its bindings, and those bindings are the secret '
                    .'and the key that encrypts it.'
                );
            }

            return $e;
        });

        $exceptions->dontReport(CredentialStorageFailed::class);

        $exceptions->shouldRenderJsonWhen(
            fn (Request $request) => $request->is('api/*') || $request->expectsJson(),
        );
    })->create();
