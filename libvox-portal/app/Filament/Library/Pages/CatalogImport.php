<?php

namespace App\Filament\Library\Pages;

use App\Database\DiscoveryScope;
use App\Filament\Library\Resources\Catalog\CatalogResource;
use BackedEnum;
use Filament\Actions\Action;
use Filament\Facades\Filament;
use Filament\Pages\Page;
use Filament\Support\Icons\Heroicon;
use Illuminate\Support\Facades\DB;

/**
 * An import, while it is happening.
 *
 * Pressing *Atualizar índice* used to return a notification and a list that
 * did not change. An import of 1,284 records takes minutes, and for those
 * minutes there was no visible difference between it running, nobody having
 * picked the request up, and it having failed — all three happened in one week.
 *
 * **The count is a fact; the bar is an estimate.** Koha's `/api/v1/biblios`
 * returns no `X-Total-Count` and no `Link` header, so there is no total to ask
 * for, and paging the whole catalogue just to count it would double the load
 * on the library's own server. What does exist is the previous generation: the
 * pipeline drops it in the same transaction that promotes the new one, so
 * throughout an import the last import's size is still countable. That is the
 * denominator, it is labelled as approximate, and a first-ever import has none.
 *
 * Not in navigation: it is reached from the button, and it is about one
 * request rather than a place to visit.
 */
class CatalogImport extends Page
{
    protected string $view = 'filament.library.pages.catalog-import';

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedArrowPath;

    protected static bool $shouldRegisterNavigation = false;

    /**
     * How long a request may sit unclaimed before the page stops assuming a
     * worker will come.
     *
     * The worker wakes on NOTIFY within milliseconds and, failing that, on its
     * own backstop poll. This has to stay **longer than that poll**, or the
     * page declares nobody is answering while a live worker is seconds from
     * picking the request up — and sends someone to restart a service that was
     * working all along. The pair were 45 and 30; they are 10 and 5.
     */
    private const NOBODY_ANSWERING_AFTER_S = 10;

    public function getTitle(): string
    {
        return __('portal.import.title');
    }

    public function getHeading(): string
    {
        return __('portal.import.title');
    }

    /** @var array<string, mixed>|null */
    private ?array $snapshot = null;

    /**
     * Everything the page needs, read once per request.
     *
     * Read once because the view asks several questions of it and a poll every
     * second should not become five round trips every second.
     */
    public function progress(): array
    {
        if ($this->snapshot !== null) {
            return $this->snapshot;
        }

        $tenantId = Filament::auth()->user()->tenant_id;

        $queuedAt = DB::table('tenant_events')
            ->where('tenant_id', $tenantId)
            ->where('event', 'SyncRequested')
            ->whereNull('consumed_at')
            ->min('occurred_at');

        // Bound here rather than in middleware, for the reason CatalogResource
        // gives: binding opens the connection, and a Discovery Index that is
        // down should cost this screen rather than the whole panel.
        app(DiscoveryScope::class)->bind($tenantId);

        $state = DB::connection('pgsql_discovery')->selectOne('
            SELECT t.index_status,
                   t.active_generation,
                   t.last_error,
                   -- Non-NULL exactly while the sync worker is importing, and
                   -- the only thing that is, for the seconds before the first
                   -- record lands. Migration 007.
                   t.import_started_at,
                   -- What the last import produced. Still here: the old
                   -- generation is dropped in the same transaction that
                   -- promotes the new one, so this survives the whole import
                   -- and is the only estimate available.
                   (SELECT count(*) FROM discovery_biblio b
                     WHERE b.generation = t.active_generation) AS previous,
                   -- The one being written, if any.
                   (SELECT max(generation) FROM discovery_biblio b
                     WHERE t.active_generation IS NULL
                        OR b.generation > t.active_generation) AS building
              FROM discovery_tenant t
             LIMIT 1
        ');

        $imported = 0;
        if ($state?->building !== null) {
            $imported = (int) DB::connection('pgsql_discovery')->scalar(
                'SELECT count(*) FROM discovery_biblio WHERE generation = ?',
                [$state->building],
            );
        }

        return $this->snapshot = [
            'state' => $this->classify($state, $queuedAt),
            'imported' => $imported,
            'previous' => (int) ($state?->previous ?? 0),
            'error' => $state?->last_error,
            'queued_at' => $queuedAt,
        ];
    }

    /**
     * Which of the five things is going on.
     *
     * Four of them have confused somebody already, which is why they are told
     * apart here rather than collapsed into "in progress".
     */
    private function classify(?object $state, ?string $queuedAt): string
    {
        // **An import in flight wins over a queued request**, and the order
        // used to be the other way round, which showed "nobody is answering"
        // over a running import. Both are true at once whenever the button is
        // pressed twice: the first press is claimed and starts writing, the
        // second sits pending until the first finishes — 50 and 91 seconds, in
        // the log that produced this fix.
        //
        // **`import_started_at` is the other half of it**, and without it this
        // screen lied for the seconds that matter most. The worker claims the
        // request the instant it is made -- so `$queuedAt` is already gone --
        // and writes its first batch a page later, so `building` is not there
        // yet. In between, every signal below said done, the page stopped
        // polling, and the librarian who had just pressed the button was
        // reading the *previous* import's total. She refreshed until it moved.
        //
        // A worker killed outright leaves the stamp set, and this screen then
        // watches an import that is not running. That is deliberate: the
        // button on the catalogue screen is left reading `index_status`, so it
        // stays pressable and the next press clears the stamp. Saying
        // "importing" for too long is recoverable; disabling the only button
        // that recovers it is not.
        if ($state?->building !== null || filled($state?->import_started_at)) {
            return 'importing';
        }

        if (filled($state?->last_error)) {
            // `building` with an error recorded is an import that stopped, not
            // one in flight: a first import has no previous generation to fall
            // back to, so the pipeline leaves the status alone. Same
            // distinction the button on the catalogue screen had to learn.
            return 'failed';
        }

        if ($queuedAt !== null) {
            return now()->diffInSeconds($queuedAt, absolute: true) > self::NOBODY_ANSWERING_AFTER_S
                ? 'unclaimed'
                : 'queued';
        }

        return $state === null ? 'queued' : 'done';
    }

    /** Whether there is still something to watch. */
    public function watching(): bool
    {
        return ! in_array($this->progress()['state'], ['done', 'failed'], true);
    }

    /**
     * How far along, as a percentage, or null when nothing says.
     *
     * Deliberately not capped at 100: a catalogue that grew since the last
     * import legitimately passes it, and a bar frozen at 99% while records keep
     * arriving is a bar that lies to look tidy.
     */
    public function percentage(): ?int
    {
        $progress = $this->progress();

        if ($progress['previous'] < 1) {
            return null;
        }

        return (int) round($progress['imported'] / $progress['previous'] * 100);
    }

    /** @return array<Action> */
    protected function getHeaderActions(): array
    {
        return [
            Action::make('backToCatalog')
                ->label(__('portal.import.back'))
                ->color('gray')
                ->url(CatalogResource::getUrl('index')),
        ];
    }
}
