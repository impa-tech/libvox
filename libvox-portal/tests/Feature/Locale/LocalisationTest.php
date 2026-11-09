<?php

use App\Models\Tenant;
use App\Support\Locale;
use App\Tenancy\TenantProvisioner;
use Illuminate\Support\Arr;
use Illuminate\Support\Facades\DB;

beforeEach(function () {
    DB::connection('pgsql_admin')->statement('TRUNCATE tenants, users, model_has_roles, roles CASCADE');
    seed_roles();
});

test('every translation key exists in both locales', function () {
    // The usual failure is adding a string in one locale and finding the raw
    // key rendered in the other, which nobody notices until a user does.
    $en = array_keys(Arr::dot(require base_path('lang/en/portal.php')));
    $pt = array_keys(Arr::dot(require base_path('lang/pt_BR/portal.php')));

    expect(array_diff($en, $pt))->toBeEmpty()
        ->and(array_diff($pt, $en))->toBeEmpty();
});

test('no user facing string is left untranslated in a panel class', function () {
    // Every label goes through __(). A literal that reaches a screen is a
    // string that can only ever be English.
    $offenders = [];

    foreach (['app/Filament', 'app/Enums'] as $directory) {
        $files = new RecursiveIteratorIterator(new RecursiveDirectoryIterator(base_path($directory)));

        foreach ($files as $file) {
            if (! $file->isFile() || $file->getExtension() !== 'php') {
                continue;
            }

            $body = file_get_contents($file->getPathname());

            // placeholder is excluded: it holds an example value -- a URL, a
            // shape -- rather than prose, and translating example.org into
            // exemplo.org.br would be inventing a domain rather than
            // translating a sentence.
            if (preg_match("/->(label|helperText|description)\(\s*'/", $body)) {
                $offenders[] = $file->getFilename();
            }
        }
    }

    expect($offenders)->toBeEmpty();
});

test('the database keeps the hyphenated tag and laravel gets the underscore', function () {
    // pt-BR crosses the wire to a browser and to /libvox-core; pt_BR is a
    // detail of one framework's directory layout and stops at this boundary.
    expect(Locale::toLaravel('pt-BR'))->toBe('pt_BR')
        ->and(Locale::toTag('pt_BR'))->toBe('pt-BR')
        ->and(Locale::other('pt-BR'))->toBe('en')
        ->and(Locale::other('en'))->toBe('pt-BR');
});

test('an unsupported locale is refused', function (?string $tag) {
    expect(Locale::isSupported($tag))->toBeFalse();
})->with(['fr', 'pt', 'pt_BR', 'en-US', '', null]);

test('a persons own preference wins over their librarys default', function () {
    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'FURG',
        'locale' => 'pt-BR',
    ]);

    $librarian = librarian('lib-furg');
    $librarian->update(['locale' => 'en']);

    // Different audiences: a librarian who set their account to English meant
    // it, even at a library whose readers are answered in Portuguese.
    $this->actingAs($librarian)->get('/app')->assertOk();

    expect(app()->getLocale())->toBe('en');
});

test('a library with no preference set falls back to its librarys language', function () {
    app(TenantProvisioner::class)->provision([
        'tenant_id' => 'lib-furg',
        'display_name' => 'FURG',
        'locale' => 'en',
    ]);

    $librarian = librarian('lib-furg');
    DB::connection('pgsql_admin')->statement(
        'UPDATE users SET locale = NULL WHERE id = ?', [$librarian->id]
    );

    $this->actingAs($librarian->fresh())->get('/app')->assertOk();

    expect(app()->getLocale())->toBe('en');
});

test('switching language stores the choice on the account', function () {
    $librarian = librarian('lib-furg');

    // Not chosen yet, which is a different thing from having chosen Portuguese.
    expect($librarian->locale)->toBeNull();

    // Stored rather than sessioned, so the choice survives signing in from
    // somewhere else.
    $this->actingAs($librarian)->post('/locale/en')->assertRedirect();

    expect($librarian->fresh()->locale)->toBe('en');
});

test('switching to a language that does not exist is refused', function () {
    $this->actingAs(librarian('lib-furg'))->post('/locale/fr')->assertNotFound();
});

test('an anonymous request cannot change anyones language', function () {
    $this->post('/locale/en')->assertForbidden();
});

test('the panels render in portuguese', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    $this->actingAs(librarian('lib-furg'))
        ->get('/app/catalog-connection')
        ->assertOk()
        ->assertSee('Conexão com o catálogo', escape: false);
});

test('the panels render in english', function () {
    app(TenantProvisioner::class)->provision(['tenant_id' => 'lib-furg', 'display_name' => 'FURG']);

    $librarian = librarian('lib-furg');
    $librarian->update(['locale' => 'en']);

    $this->actingAs($librarian)
        ->get('/app/catalog-connection')
        ->assertOk()
        ->assertSee('Catalog connection', escape: false);
});

test('a tenant cannot be given a language nothing translates', function () {
    // The CHECK on tenants.locale and the SUPPORTED list are two statements of
    // one fact, and this is what keeps them from drifting apart.
    expect(Locale::SUPPORTED)->toBe(['pt-BR', 'en']);

    expect(fn () => Tenant::create([
        'tenant_id' => 'lib-outra',
        'display_name' => 'Outra',
        'locale' => 'fr-FR',
    ]))->toThrow(Exception::class);
});
