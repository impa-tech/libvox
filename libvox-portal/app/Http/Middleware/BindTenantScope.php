<?php

namespace App\Http\Middleware;

use App\Database\TenantScope;
use App\Models\User;
use Closure;
use Filament\Facades\Filament;
use Illuminate\Http\Request;
use Symfony\Component\HttpFoundation\Response;

/**
 * Every request to the library panel runs bound to that librarian's library.
 *
 * The tenant comes from the authenticated user and from nowhere else -- not a
 * route segment, not a header, not a form field. Same reasoning that keeps
 * tenant_id out of the MCP tool schemas: anything the request can carry is
 * something the request can change.
 *
 * Registered as auth middleware **and as persistent**, which is the half that
 * is easy to miss and fails in a way that points nowhere near the cause.
 *
 * Livewire's update route is registered globally, with `web` and nothing else:
 * no panel middleware runs on it. So the first GET of a page is scoped and
 * every interaction after it is not -- a form that renders correctly and then
 * refuses to save, with row-level security named in the error and the
 * middleware apparently in place. `isPersistent: true` is what makes Filament
 * re-apply this to a Livewire update.
 *
 * It binds as a side effect rather than by wrapping `$next`, and that is not a
 * style choice. Livewire re-applies persistent middleware through a pipeline
 * whose destination returns an empty response immediately, so a wrapper would
 * release its binding before the component ever ran. See TenantScope.
 */
class BindTenantScope
{
    public function handle(Request $request, Closure $next): Response
    {
        $user = Filament::auth()->user();

        // canAccessPanel already refuses a librarian without a library, and it
        // is the right place for that refusal -- it is an authorization
        // question. This is the second reading of the same fact, at the point
        // where its absence would matter: binding to nothing would leave the
        // request unscoped, and an unscoped session reads every tenant table as
        // empty, which looks like data loss rather than a misconfiguration.
        abort_unless($user instanceof User && $user->tenant_id !== null, 403);

        app(TenantScope::class)->bind($user->tenant_id);

        // The Discovery Index is not bound here. `bind` applies immediately,
        // which opens that connection -- and it would then open on every
        // request to this panel, so a Discovery Index that is down would take
        // the whole panel with it rather than one screen. It is bound where it
        // is read; see CatalogResource.
        return $next($request);
    }

    /**
     * Release when the request is finished with.
     *
     * Not a finally block around $next: a streamed response finishes after
     * handle() returns, and the binding has to outlive it. Not the only thing
     * keeping a binding from leaking either -- the next request through this
     * panel overwrites it, and the connection closes at the end of a request.
     * This is the one that runs even when nothing else does.
     */
    public function terminate(Request $request, Response $response): void
    {
        app(TenantScope::class)->release();
    }
}
