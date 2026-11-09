<?php

namespace App\Providers\Filament;

use App\Filament\Library\Pages\EditProfile;
use App\Http\Middleware\BindTenantScope;
use App\Http\Middleware\SetLocale;
use App\Support\Locale;
use Filament\Http\Middleware\Authenticate;
use Filament\Http\Middleware\AuthenticateSession;
use Filament\Http\Middleware\DisableBladeIconComponents;
use Filament\Http\Middleware\DispatchServingFilamentEvent;
use Filament\Navigation\MenuItem;
use Filament\Pages\Dashboard;
use Filament\Panel;
use Filament\PanelProvider;
use Filament\Support\Colors\Color;
use Filament\View\PanelsRenderHook;
use Illuminate\Contracts\View\View;
use Illuminate\Cookie\Middleware\AddQueuedCookiesToResponse;
use Illuminate\Cookie\Middleware\EncryptCookies;
use Illuminate\Foundation\Http\Middleware\PreventRequestForgery;
use Illuminate\Routing\Middleware\SubstituteBindings;
use Illuminate\Session\Middleware\StartSession;
use Illuminate\View\Middleware\ShareErrorsFromSession;

/**
 * The library's own panel: one librarian, one library, its configuration.
 *
 * Filament's built-in multi-tenancy is deliberately not used here. It models a
 * user who belongs to several tenants and switches between them: it puts the
 * tenant in the URL as /app/{tenant}/..., renders a switcher, and scopes
 * through a global Eloquent scope. Here a librarian belongs to exactly one
 * library by a foreign key, the tenant must never be a URL segment a browser
 * can edit, and the scoping is PostgreSQL row-level security rather than a
 * query builder someone can forget to apply.
 */
class LibraryPanelProvider extends PanelProvider
{
    public function panel(Panel $panel): Panel
    {
        return $panel
            ->id('library')
            ->path('app')
            ->login()
            // The same compiled stylesheet the operator's panel loads. The
            // colours still differ -- those are custom properties Filament
            // emits at runtime from `->colors()` below, not anything baked in.
            ->viteTheme('resources/css/filament/theme.css')
            // Her own password, and nothing else on that page -- see
            // EditProfile for why the email address is not editable here.
            // `isSimple: false` keeps the panel's navigation around it: this is
            // a screen inside her work, not a step in signing in.
            ->profile(EditProfile::class, isSimple: false)
            ->brandName('LibVox')
            ->colors([
                'primary' => Color::Indigo,
            ])
            ->discoverResources(in: app_path('Filament/Library/Resources'), for: 'App\Filament\Library\Resources')
            ->discoverPages(in: app_path('Filament/Library/Pages'), for: 'App\Filament\Library\Pages')
            ->pages([
                Dashboard::class,
            ])
            ->discoverWidgets(in: app_path('Filament/Library/Widgets'), for: 'App\Filament\Library\Widgets')
            ->middleware([
                EncryptCookies::class,
                AddQueuedCookiesToResponse::class,
                StartSession::class,
                AuthenticateSession::class,
                ShareErrorsFromSession::class,
                PreventRequestForgery::class,
                SubstituteBindings::class,
                DisableBladeIconComponents::class,
                DispatchServingFilamentEvent::class,
                // After the session starts, so a signed-in person's own
                // preference is available to read.
                SetLocale::class,
            ])
            ->userMenuItems([
                // Two languages, so a switcher is a toggle rather than a
                // picker: the label names where it goes, not where you are.
                MenuItem::make()
                    ->label(fn (): string => __('portal.locale.switch_to'))
                    ->icon('heroicon-o-language')
                    // postAction takes the URL to POST to. A GET that changes a
                    // stored preference would be followed by every link
                    // prefetcher the browser has.
                    ->postAction(fn (): string => route('locale.switch', [
                        'tag' => Locale::other(Locale::toTag(app()->getLocale())),
                    ])),
            ])
            // Who is signed in, and which library this session is bound to.
            // The second half is not decoration: the tenant is deliberately
            // absent from the URL, so without this nothing on screen says which
            // library a page is about -- and that identifier is what every
            // other LibVox component calls her library.
            ->renderHook(
                PanelsRenderHook::FOOTER,
                fn (): View => view('filament.library.status-bar'),
            )
            // The AGPL's offer of source, under the status bar. A second hook
            // on the same position rather than a line inside the first: that
            // one renders only for a librarian bound to a library, and this
            // one is about the software, so it is shown to whoever is looking.
            ->renderHook(
                PanelsRenderHook::FOOTER,
                fn (): View => view('filament.source-link'),
            )
            ->authMiddleware([
                Authenticate::class,
                // After Authenticate, because the tenant comes from the user.
                BindTenantScope::class,
            ], isPersistent: true);
    }
}
