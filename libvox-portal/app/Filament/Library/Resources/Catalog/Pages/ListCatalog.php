<?php

namespace App\Filament\Library\Resources\Catalog\Pages;

use App\Filament\Library\Pages\CatalogImport;
use App\Filament\Library\Resources\Catalog\CatalogResource;
use App\Models\Tenant;
use App\Support\CatalogCsv;
use Filament\Actions\Action;
use Filament\Notifications\Notification;
use Filament\Resources\Pages\ListRecords;
use Filament\Support\Icons\Heroicon;
use Illuminate\Support\Carbon;
use Illuminate\Support\Facades\DB;

class ListCatalog extends ListRecords
{
    protected static string $resource = CatalogResource::class;

    public function getTitle(): string
    {
        return __('portal.catalog.label_plural');
    }

    /**
     * Ask for an import, and watch it.
     *
     * `request_catalog_sync()` takes no argument, and that is the guarantee
     * rather than a convenience: the library is whichever one this request is
     * bound to, so there is nowhere to write another library's name. It
     * answers false when one is already pending, when no ILS is configured, or
     * when nothing is bound -- three refusals the panel does not have to know
     * apart, because none of them should start an import.
     */
    protected function getHeaderActions(): array
    {
        return [
            Action::make('requestSync')
                ->label(__('portal.catalog.request'))
                ->icon(Heroicon::OutlinedArrowPath)
                ->requiresConfirmation()
                ->modalDescription(fn () => $this->confirmationText())
                ->disabled(fn (): bool => ! $this->canRequest())
                ->action(function () {
                    $asked = DB::selectOne('SELECT request_catalog_sync() AS ok')->ok;

                    if (! $asked) {
                        Notification::make()
                            ->title(__('portal.catalog.not_requested'))
                            ->body(__('portal.catalog.not_requested_help'))
                            ->warning()
                            ->send();

                        return null;
                    }

                    // A notification and a list that does not change was the
                    // whole problem: an import takes minutes, and nothing on
                    // this screen moved during them.
                    return redirect(CatalogImport::getUrl());
                }),

            // The catalogue as a file, for the questions this screen cannot
            // answer. The table searches the way MARC.IA searches, one query
            // at a time; counting how much of the collection has no author,
            // or which item types exist at all, is a spreadsheet's job and
            // never a panel's.
            //
            // No confirmation: unlike the button above it, this asks nothing
            // of the library's own server. It reads the index and nothing
            // else.
            Action::make('exportCsv')
                ->label(__('portal.catalog.export'))
                ->icon(Heroicon::OutlinedArrowDownTray)
                ->disabled(fn (): bool => ! $this->everImported())
                ->tooltip(fn (): ?string => $this->everImported()
                    ? null
                    : __('portal.catalog.export_nothing'))
                ->action(fn () => CatalogCsv::download(auth()->user()->tenant_id)),
        ];
    }

    /**
     * Whether there is anything to export.
     *
     * `last_sync_at` rather than a count: an index that has never been
     * imported has no active generation, and exporting it hands someone a file
     * of column headings and no explanation. Counting the rows to find that
     * out would be a full scan on every render of the page.
     */
    private function everImported(): bool
    {
        return filled($this->indexState()?->last_sync_at);
    }

    /**
     * Whether pressing it could do anything.
     *
     * A button that starts hundreds of requests against a library's own server
     * should not be pressable when the answer is already known: nothing to
     * import, one already asked for, or one already running.
     */
    private function canRequest(): bool
    {
        $tenant = Tenant::query()->find(auth()->user()->tenant_id);

        if ($tenant?->ils_configured_at === null) {
            return false;
        }

        $pending = DB::table('tenant_events')
            ->where('tenant_id', $tenant->tenant_id)
            ->where('event', 'SyncRequested')
            ->whereNull('consumed_at')
            ->exists();

        return ! $pending && ! $this->importRunning();
    }

    /**
     * Whether an import is actually under way.
     *
     * Not simply `index_status === 'building'`, which was the bug: a **first**
     * import that fails stays `building` forever, because there is no previous
     * generation to fall back to and `building` is the honest thing to tell
     * MARC.IA about an index that has never been published. Reading that as
     * "in progress" disabled the only button that could fix it, permanently.
     *
     * `last_error` is what tells them apart. The pipeline writes it on every
     * failure, so `building` with an error recorded is an import that stopped,
     * and `building` with none is one still running.
     */
    private function importRunning(): bool
    {
        $state = $this->indexState();

        return $state?->index_status === 'building' && blank($state->last_error);
    }

    /**
     * What the confirmation dialog says, which depends on what happened last.
     *
     * Not a cooling-off window: blocking would punish exactly the person who
     * just fixed the credential and wants to try. What was missing is the
     * information at the moment of the decision — she is about to send hundreds
     * of requests to a server that failed two minutes ago, and the dialog said
     * only that imports are expensive.
     */
    public function confirmationText(): string
    {
        $state = $this->indexState();

        if (blank($state?->last_error)) {
            return __('portal.catalog.request_confirm');
        }

        return __('portal.catalog.request_confirm_after_failure', [
            'when' => $state->last_sync_at
                ? Carbon::parse($state->last_sync_at)->diffForHumans()
                : __('portal.catalog.never'),
            'error' => $state->last_error,
        ]);
    }

    /** What the Discovery Index says about this library, or null. */
    private function indexState(): ?object
    {
        return DB::connection('pgsql_discovery')->selectOne(
            'SELECT index_status, last_sync_at, last_error FROM discovery_tenant LIMIT 1'
        );
    }

    public function getSubheading(): ?string
    {
        $state = $this->indexState();

        if ($state === null) {
            return __('portal.catalog.never_synced');
        }

        $when = $state->last_sync_at
            ? Carbon::parse($state->last_sync_at)->diffForHumans()
            : __('portal.catalog.never');

        // A failed first import is `building` with an error, and calling that
        // "being built" reads as patience when what is needed is another
        // attempt.
        $line = filled($state->last_error) && $state->index_status === 'building'
            ? __('portal.catalog.first_import_failed')
            : __('portal.catalog.state', [
                'status' => __('portal.catalog.status.'.$state->index_status),
                'when' => $when,
            ]);

        // The reason, verbatim, when there is one.
        //
        // It names the library's own Koha and the status it answered, and
        // never a secret -- the driver does not echo a response body. Hiding
        // it would be applying the rule that protects a *patron* to the person
        // who owns the server: "the API user needs Koha's catalogue flag" is
        // the only sentence that fixes anything, and she is the only one who
        // can act on it.
        if (filled($state->last_error)) {
            $line .= ' '.$state->last_error;
        }

        return $line;
    }
}
