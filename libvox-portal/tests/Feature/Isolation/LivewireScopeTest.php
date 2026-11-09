<?php

use App\Database\TenantScope;
use App\Http\Middleware\BindTenantScope;
use App\Models\TenantWidget;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Facades\DB;
use Livewire\Drawer\Utils;
use Livewire\Livewire;
use Livewire\Mechanisms\HandleRequests\HandleRequests;
use Symfony\Component\HttpKernel\Exception\HttpException;

/*
 * The half of the tenant binding that is easy to miss.
 *
 * Livewire's update route is registered globally, with `web` and nothing else.
 * No panel middleware runs on it -- so a page's first GET is scoped and every
 * interaction after it is not, which presents as a form that renders correctly
 * and then refuses to save. Filament's answer is persistent middleware, and
 * these are the tests that would have caught its absence.
 *
 * They are structural rather than functional because the alternative -- POSTing
 * a hand-built Livewire snapshot -- tests the payload format more than the
 * binding, and Livewire::test() runs no middleware at all, so it cannot see
 * this either.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);
});

test('the tenant binding is re-applied to livewire updates', function () {
    // The single fact that was missing. Without it, everything the library
    // panel writes is refused and everything it reads comes back empty.
    expect(Livewire::getPersistentMiddleware())->toContain(BindTenantScope::class);
});

test('the livewire update route carries no panel middleware of its own', function () {
    // Asserted so the reasoning above cannot quietly stop being true: if a
    // future Livewire or Filament release starts applying panel middleware
    // here, this fails and the persistent registration can be reconsidered
    // rather than left as cargo.
    $route = app(HandleRequests::class)->getUpdateUri();
    $middleware = app('router')->getRoutes()->getByAction(
        Livewire\Mechanisms\HandleRequests\HandleRequests::class.'@handleUpdate'
    )?->gatherMiddleware() ?? [];

    expect($route)->toBeString()
        ->and($middleware)->not->toContain(BindTenantScope::class);
});

test('a librarian request is bound, and the binding is released with it', function () {
    $scope = app(TenantScope::class);

    expect($scope->current())->toBeNull();

    // The middleware reads Filament::auth(), so the panel has to be resolved
    // -- outside an HTTP request nothing else says which one.
    Filament\Facades\Filament::setCurrentPanel('library');
    test()->actingAs(librarian('lib-furg'));

    $bound = null;
    $middleware = new BindTenantScope;
    $middleware->handle(
        request(),
        function () use ($scope, &$bound) {
            $bound = $scope->current();

            // The write that was failing: inside the binding it is allowed.
            TenantWidget::updateOrCreate(['tenant_id' => 'lib-furg'], ['assistant_name' => 'Bibi']);

            return response('ok');
        },
    );

    // Bound for the whole request, and still bound when handle() returns: a
    // streamed response finishes after that, and the component the binding
    // exists for runs after it too.
    expect($bound)->toBe('lib-furg')
        ->and($scope->current())->toBe('lib-furg');

    $middleware->terminate(request(), response('ok'));

    expect($scope->current())->toBeNull();

    $saved = $scope->run('lib-furg', fn () => TenantWidget::find('lib-furg'));
    expect($saved->assistant_name)->toBe('Bibi');
});

test('an operator never reaches the binding', function () {
    // The library panel is the only place it is registered, and an operator
    // cannot reach that panel. Binding one would be binding to nothing.
    Filament\Facades\Filament::setCurrentPanel('library');
    test()->actingAs(operator());

    expect(fn () => (new BindTenantScope)->handle(
        request(),
        fn () => response('ok'),
    ))->toThrow(HttpException::class);
});

test('the binding survives the pipeline livewire re-applies it through', function () {
    /*
     * The shape of the defect this file exists for, reproduced.
     *
     * Livewire re-applies persistent middleware with
     * Utils::applyMiddleware(), a pipeline whose destination is
     * `fn () => new Response()` -- it returns immediately, before the component
     * runs. A middleware that binds by wrapping $next therefore opens its
     * scope, receives that empty response, and releases the binding it was
     * meant to leave in place. Marking it persistent was necessary and not
     * sufficient; every write on the library panel was still refused.
     *
     * So the test is not "does the middleware run" but "is anything still
     * bound once the pipeline has returned".
     */
    Filament\Facades\Filament::setCurrentPanel('library');
    test()->actingAs(librarian('lib-furg'));

    $scope = app(TenantScope::class);
    $scope->release();

    Utils::applyMiddleware(request(), [BindTenantScope::class]);

    expect($scope->current())->toBe('lib-furg');

    // And the database agrees, which is the half that actually decides.
    $bound = DB::connection('pgsql')->selectOne(
        "select current_setting('libvox.tenant_id', true) as tenant"
    );

    expect($bound->tenant)->toBe('lib-furg')
        ->and(TenantWidget::count())->toBe(1);
});

test('a write refused before the fix now goes through', function () {
    // The exact statement from the report: an insert into tenant_widget for a
    // library, from a request bound the way Livewire binds one.
    Filament\Facades\Filament::setCurrentPanel('library');
    test()->actingAs(librarian('lib-furg'));

    app(TenantScope::class)->release();
    Utils::applyMiddleware(request(), [BindTenantScope::class]);

    TenantWidget::updateOrCreate(['tenant_id' => 'lib-furg'], ['assistant_name' => 'Bibi']);

    expect(TenantWidget::find('lib-furg')->assistant_name)->toBe('Bibi');
});
