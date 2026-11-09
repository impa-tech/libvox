<?php

namespace App\Filament\Library\Widgets;

use App\Database\DiscoveryScope;
use App\Filament\Library\Resources\Catalog\CatalogResource;
use Filament\Facades\Filament;
use Filament\Support\Icons\Heroicon;
use Filament\Widgets\StatsOverviewWidget;
use Filament\Widgets\StatsOverviewWidget\Stat;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;
use Illuminate\Support\Facades\Log;
use Throwable;

/**
 * What MARC.IA can find, on the first screen a librarian sees.
 *
 * Two numbers: how many records are in the index, and when they were last read
 * from the ILS. Both are about the *index*, not the catalogue — a distinction
 * this widget has to carry rather than blur, because a librarian looking at
 * "1.462 registros" needs to know whether that is what her readers are being
 * answered from today or a copy from last month.
 *
 * **It must never take the dashboard down with it.** This is the landing page,
 * and the Discovery Index is a second database that /libvox-sync owns and this
 * application only reads. Everything below is wrapped: a database that cannot
 * be reached costs this card and nothing else.
 */
class CatalogOverview extends StatsOverviewWidget
{
    protected int|string|array $columnSpan = 'full';

    protected function getStats(): array
    {
        $state = $this->indexState();

        if ($state === null) {
            return [
                Stat::make(__('portal.overview.records'), '—')
                    ->description(__('portal.overview.unreachable'))
                    ->descriptionIcon(Heroicon::OutlinedExclamationTriangle)
                    ->color('gray'),
            ];
        }

        if ($state->active_generation === null) {
            // Never imported, which is a different thing from empty: an empty
            // catalogue is an answer, and this is the absence of one.
            return [
                Stat::make(__('portal.overview.records'), '0')
                    ->description(__('portal.overview.never_imported'))
                    ->descriptionIcon(Heroicon::OutlinedArrowPath)
                    ->descriptionColor('warning')
                    ->url(CatalogResource::getUrl('index')),
            ];
        }

        return [
            Stat::make(
                __('portal.overview.records'),
                number_format((int) $state->records, 0, ',', '.'),
            )
                ->description(__('portal.overview.records_help'))
                ->descriptionIcon(Heroicon::OutlinedBookOpen)
                ->url(CatalogResource::getUrl('index')),

            Stat::make(
                __('portal.overview.last_import'),
                $state->last_sync_at
                    ? Carbon::parse($state->last_sync_at)->diffForHumans()
                    : __('portal.overview.never'),
            )
                // The status belongs *here*, next to the date, because that is
                // the pair that means something: "ontem" over a stale index is
                // "the last attempt was yesterday and it failed", which reads
                // nothing like "ontem" over a current one.
                ->description($this->freshness($state))
                ->descriptionIcon(
                    $state->index_status === 'ready'
                        ? Heroicon::OutlinedCheckCircle
                        : Heroicon::OutlinedExclamationTriangle
                )
                ->descriptionColor($state->index_status === 'ready' ? 'success' : 'warning')
                ->url(CatalogResource::getUrl('index')),
        ];
    }

    private function freshness(object $state): string
    {
        if ($state->index_status === 'ready') {
            return __('portal.overview.current');
        }

        // `building` with an error recorded is an import that stopped, not one
        // in flight — the same distinction the catalogue screen and its button
        // both had to learn.
        if (filled($state->last_error)) {
            return __('portal.overview.last_attempt_failed');
        }

        return __('portal.catalog.status.'.$state->index_status);
    }

    /**
     * The index's state, or null when it cannot be read.
     *
     * Bound here rather than in middleware, for the reason CatalogResource
     * gives: binding opens the connection, and this widget opening it on the
     * dashboard is what would make a Discovery Index that is down cost every
     * screen instead of this card.
     */
    private function indexState(): ?object
    {
        try {
            app(DiscoveryScope::class)->bind(Filament::auth()->user()->tenant_id);

            return DB::connection('pgsql_discovery')->selectOne('
                SELECT t.index_status,
                       t.active_generation,
                       t.last_sync_at,
                       t.last_error,
                       (SELECT count(*) FROM discovery_biblio b
                         WHERE b.generation = t.active_generation) AS records
                  FROM discovery_tenant t
                 LIMIT 1
            ') ?? (object) [
                // No row at all: /libvox-sync has never touched this library.
                'index_status' => 'building',
                'active_generation' => null,
                'last_sync_at' => null,
                'last_error' => null,
                'records' => 0,
            ];
        } catch (Throwable $exception) {
            Log::warning('the dashboard could not read the Discovery Index', [
                'error' => $exception->getMessage(),
            ]);

            return null;
        }
    }
}
