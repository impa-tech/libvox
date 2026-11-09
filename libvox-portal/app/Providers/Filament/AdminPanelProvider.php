<?php

namespace App\Providers\Filament;

use App\Http\Middleware\SetLocale;
use App\Support\Locale;
use BezhanSalleh\FilamentShield\FilamentShieldPlugin;
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
 * The operator's panel: LibVox staff, registering and supporting libraries.
 *
 * Separate from the library panel by path, by resource namespace and by role.
 * The two never share a screen, which is why they never share a directory --
 * a resource dropped into Filament/Admin cannot appear in the other panel by
 * accident, because the other panel does not look there.
 */
class AdminPanelProvider extends PanelProvider
{
    public function panel(Panel $panel): Panel
    {
        return $panel
            ->default()
            ->id('admin')
            ->path('admin')
            // Shared with the library panel; see the file for why one and not two.
            ->viteTheme('resources/css/filament/theme.css')
            ->login()
            ->brandName('LibVox — Operations')
            ->colors([
                'primary' => Color::Amber,
            ])
            ->discoverResources(in: app_path('Filament/Admin/Resources'), for: 'App\Filament\Admin\Resources')
            ->discoverPages(in: app_path('Filament/Admin/Pages'), for: 'App\Filament\Admin\Pages')
            ->pages([
                Dashboard::class,
            ])
            ->discoverWidgets(in: app_path('Filament/Admin/Widgets'), for: 'App\Filament\Admin\Widgets')
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
            // The AGPL's offer of source. The librarian's panel carries the
            // same one; an operator is no less a user of the software.
            ->renderHook(
                PanelsRenderHook::FOOTER,
                fn (): View => view('filament.source-link'),
            )
            ->authMiddleware([
                Authenticate::class,
            ])
            ->plugins([
                FilamentShieldPlugin::make(),
            ]);
    }
}
