<?php

namespace App\Filament\Library\Resources\Catalog;

use App\Filament\Library\Resources\Catalog\Pages\ListCatalog;
use App\Filament\Library\Resources\Catalog\Tables\CatalogTable;
use App\Models\CatalogRecord;
use BackedEnum;
use Filament\Resources\Resource;
use Filament\Support\Icons\Heroicon;
use Filament\Tables\Table;
use Illuminate\Database\Eloquent\Builder;

/**
 * What MARC.IA can find — which is not the same question as what the library
 * holds.
 *
 * That difference is the reason this screen exists. A librarian who asks "why
 * did she not find my book?" has, until now, had no way to look. Here she can:
 * a record missing from this list is a record MARC.IA cannot see, and the
 * answer is either an import that has not run or a book the ILS does not
 * expose.
 *
 * Read-only, and nothing here can change it. The Discovery Index is rebuilt
 * from the ILS by /libvox-sync; a correction belongs in the catalogue, not in
 * a cache of it.
 */
class CatalogResource extends Resource
{
    protected static ?string $model = CatalogRecord::class;

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedBookOpen;

    protected static ?int $navigationSort = 20;

    public static function table(Table $table): Table
    {
        return CatalogTable::configure($table);
    }

    /**
     * Bind the Discovery connection here rather than in the request middleware.
     *
     * Binding applies immediately, which opens that connection — so binding it
     * for every request would let a Discovery Index that is down take the whole
     * panel with it instead of this one screen. This runs when the catalogue is
     * read and at no other time.
     *
     * The tenant is not passed to the query. It is bound on the connection, and
     * the policy on `discovery_biblio` does the filtering: a forgotten binding
     * reads empty, never another library's shelves.
     */
    public static function getEloquentQuery(): Builder
    {
        CatalogRecord::scopeToTenant(auth()->user()->tenant_id);

        return parent::getEloquentQuery();
    }

    public static function getModelLabel(): string
    {
        return __('portal.catalog.label');
    }

    public static function getPluralModelLabel(): string
    {
        return __('portal.catalog.label_plural');
    }

    public static function getNavigationLabel(): string
    {
        return __('portal.catalog.label_plural');
    }

    public static function canCreate(): bool
    {
        return false;
    }

    public static function getPages(): array
    {
        return [
            'index' => ListCatalog::route('/'),
        ];
    }
}
