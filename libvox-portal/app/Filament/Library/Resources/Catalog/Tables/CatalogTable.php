<?php

namespace App\Filament\Library\Resources\Catalog\Tables;

use App\Models\CatalogRecord;
use Filament\Actions\ViewAction;
use Filament\Infolists\Components\TextEntry;
use Filament\Schemas\Components\Section;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Table;
use Illuminate\Database\Eloquent\Builder;

class CatalogTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('title')
                    ->label(__('portal.catalog.title'))
                    ->wrap()
                    ->weight('medium')
                    // The whole point of the screen: this searches the way
                    // MARC.IA searches, not the way a LIKE would. A title she
                    // finds is a title found here, and a word that turns up
                    // nothing here turns up nothing for a reader either.
                    ->searchable(query: self::likeMarcia(...)),

                TextColumn::make('author')
                    ->label(__('portal.catalog.author'))
                    ->placeholder('—')
                    ->toggleable(),

                TextColumn::make('series')
                    ->label(__('portal.catalog.series'))
                    ->placeholder('—')
                    ->toggleable(),

                TextColumn::make('publisher')
                    ->label(__('portal.catalog.publisher'))
                    ->placeholder('—')
                    ->toggleable(isToggledHiddenByDefault: true),

                TextColumn::make('copyright_date')
                    ->label(__('portal.catalog.year'))
                    ->placeholder('—')
                    ->sortable(),

                TextColumn::make('item_type')
                    ->label(__('portal.catalog.item_type'))
                    ->badge()
                    ->placeholder('—')
                    ->toggleable(),

                TextColumn::make('biblio_id')
                    ->label(__('portal.catalog.biblio_id'))
                    ->sortable()
                    ->toggleable(),

                TextColumn::make('ils_timestamp')
                    ->label(__('portal.catalog.changed'))
                    ->dateTime('d/m/Y H:i')
                    ->placeholder('—')
                    ->sortable()
                    ->toggleable(isToggledHiddenByDefault: true),
            ])
            ->defaultSort('title')
            ->searchPlaceholder(__('portal.catalog.search'))
            ->emptyStateHeading(__('portal.catalog.empty'))
            ->emptyStateDescription(__('portal.catalog.empty_help'))
            // Read, but not create, edit or delete: this is a read model
            // rebuilt from the ILS, and a correction made here would be undone
            // by the next import without telling anyone.
            ->recordActions([
                ViewAction::make()
                    ->label(__('portal.catalog.view'))
                    ->modalHeading(fn (CatalogRecord $record): string => $record->title)
                    ->modalSubmitAction(false)
                    ->schema(self::everythingIndexed()),
            ])
            ->toolbarActions([]);
    }

    /**
     * Every field the index holds for one record.
     *
     * **Not the ILS record.** This is the read model MARC.IA answers from, so
     * what is absent here is absent for a reader too — which is the question
     * this screen exists to answer, and the reason a blank is shown as a blank
     * rather than hidden. A librarian looking for why a book is not being found
     * needs to see the empty author, not a tidy row without one.
     *
     * `abstract` and `notes` come from `CatalogRecord::indexedText()`, and they
     * are the point of the modal. Both are indexed at weight D and neither is
     * ever returned to the model, so a record that matched on a word appearing
     * only in its abstract is otherwise inexplicable from the listing.
     *
     * @return array<mixed>
     */
    private static function everythingIndexed(): array
    {
        return [
            Section::make(__('portal.catalog.section_record'))
                ->columns(2)
                ->schema([
                    TextEntry::make('biblio_id')
                        ->label(__('portal.catalog.biblio_id'))
                        ->helperText(__('portal.catalog.biblio_id_help'))
                        ->copyable(),
                    TextEntry::make('item_type')
                        ->label(__('portal.catalog.item_type'))
                        ->badge()
                        ->placeholder('—'),
                    TextEntry::make('title')
                        ->label(__('portal.catalog.title'))
                        ->columnSpanFull(),
                    TextEntry::make('author')
                        ->label(__('portal.catalog.author'))
                        ->placeholder('—'),
                    TextEntry::make('series')
                        ->label(__('portal.catalog.series'))
                        ->placeholder('—'),
                    TextEntry::make('publisher')
                        ->label(__('portal.catalog.publisher'))
                        ->placeholder('—'),
                    TextEntry::make('copyright_date')
                        ->label(__('portal.catalog.year'))
                        ->placeholder('—'),
                    TextEntry::make('isbn')
                        ->label(__('portal.catalog.isbn'))
                        ->placeholder('—')
                        ->copyable(),
                    TextEntry::make('ils_timestamp')
                        ->label(__('portal.catalog.changed'))
                        ->dateTime('d/m/Y H:i')
                        ->placeholder('—'),
                ]),

            Section::make(__('portal.catalog.section_searchable'))
                ->description(__('portal.catalog.section_searchable_help'))
                ->schema([
                    TextEntry::make('abstract')
                        ->label(__('portal.catalog.abstract'))
                        ->state(fn (CatalogRecord $record) => $record->indexedText()['abstract'])
                        ->placeholder(__('portal.catalog.not_recorded'))
                        ->columnSpanFull(),
                    TextEntry::make('notes')
                        ->label(__('portal.catalog.notes'))
                        ->state(fn (CatalogRecord $record) => $record->indexedText()['notes'])
                        ->placeholder(__('portal.catalog.not_recorded'))
                        ->columnSpanFull(),
                ]),
        ];
    }

    /**
     * Full-text search, in the configuration the Discovery Index was built
     * with.
     *
     * `libvox_search` is unaccent + Portuguese stemming, so "pokemon" finds
     * "Pokémon" and "memorias" finds "Memórias" — and it reaches the series and
     * the description as well as the title, which is why a search here can turn
     * up a book whose title contains none of the words typed.
     *
     * `plainto_tsquery`, never `to_tsquery`: the second parses operators, and a
     * librarian typing `chess & !duchess` would get a syntax error rather than
     * a search. The same choice /libvox-sync makes for the same reason.
     */
    private static function likeMarcia(Builder $query, string $search): Builder
    {
        return $query
            ->whereRaw("search @@ plainto_tsquery('libvox_search', ?)", [$search])
            ->orderByRaw("ts_rank(search, plainto_tsquery('libvox_search', ?)) DESC", [$search]);
    }
}
