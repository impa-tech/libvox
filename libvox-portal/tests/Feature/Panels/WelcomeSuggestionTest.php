<?php

use App\Database\TenantScope;
use App\Filament\Library\Pages\WidgetSettings;
use App\Models\Tenant;
use App\Models\TenantWidget;
use App\Models\User;
use Filament\Facades\Filament;
use Illuminate\Support\Facades\DB;

/*
 * The greeting a librarian starts from.
 *
 * A suggestion and not a default: it reaches the form, she edits or clears it,
 * and what she saves is what a reader sees. Nothing regenerates it afterwards,
 * because a greeting she wrote is hers.
 */

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
    Tenant::create(['tenant_id' => 'lib-furg', 'display_name' => 'Biblioteca Central da FURG']);
    Filament::setCurrentPanel('library');
});

/**
 * The librarian, created once.
 *
 * `librarian()` uses a fixed email address, so calling it twice in one test
 * violates a unique constraint and aborts the transaction the tenant scope is
 * holding -- which surfaces as a failure on the release at the end, naming
 * neither the insert nor the email.
 */
function theLibrarian(): User
{
    static $user = null;

    return $user ??= librarian('lib-furg');
}

function openWidgetSettings(): array
{
    return app(TenantScope::class)->run('lib-furg', function () {
        $page = Livewire\Livewire::actingAs(theLibrarian())->test(WidgetSettings::class);

        return $page->instance()->data;
    });
}

test('an empty greeting is filled with a suggestion in the librarys own name', function () {
    $state = openWidgetSettings();

    expect($state['welcome_message'])
        ->toContain('Biblioteca Central da FURG')
        ->toContain('MARC.IA');
});

test('it uses the display name, not the tenant id', function () {
    // The failure it exists to prevent, seen in the wild: a widget welcoming
    // readers to "biblioteca demofranca" for a library called NARC, because
    // the name was typed by hand rather than read from the registry.
    $state = openWidgetSettings();

    expect($state['welcome_message'])->not->toContain('lib-furg');
});

test('a greeting the librarian wrote is left alone', function () {
    app(TenantScope::class)->run('lib-furg', fn () => TenantWidget::create([
        'tenant_id' => 'lib-furg',
        'assistant_name' => 'Bibi',
        'welcome_message' => 'Oi! Pergunte o que quiser.',
    ]));

    expect(openWidgetSettings()['welcome_message'])->toBe('Oi! Pergunte o que quiser.');
});

test('the suggestion carries the assistants configured name', function () {
    app(TenantScope::class)->run('lib-furg', fn () => TenantWidget::create([
        'tenant_id' => 'lib-furg',
        'assistant_name' => 'Bibi',
    ]));

    expect(openWidgetSettings()['welcome_message'])->toContain('Bibi');
});

test('saving it stores exactly what was shown', function () {
    // Otherwise the suggestion is decoration: the field would look filled and
    // a reader would still be greeted by nothing.
    $suggested = openWidgetSettings()['welcome_message'];

    app(TenantScope::class)->run('lib-furg', fn () => Livewire\Livewire::actingAs(theLibrarian())
        ->test(WidgetSettings::class)
        ->call('save')
        ->assertHasNoFormErrors());

    $stored = app(TenantScope::class)->run('lib-furg', fn () => TenantWidget::find('lib-furg'));

    expect($stored->welcome_message)->toBe($suggested);
});
