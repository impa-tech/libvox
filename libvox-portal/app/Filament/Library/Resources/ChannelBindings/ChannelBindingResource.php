<?php

namespace App\Filament\Library\Resources\ChannelBindings;

use App\Filament\Library\Resources\ChannelBindings\Pages\EditChannelBinding;
use App\Filament\Library\Resources\ChannelBindings\Pages\ListChannelBindings;
use App\Filament\Library\Resources\ChannelBindings\Schemas\ChannelBindingForm;
use App\Filament\Library\Resources\ChannelBindings\Tables\ChannelBindingsTable;
use App\Models\ChannelBinding;
use BackedEnum;
use Filament\Resources\Resource;
use Filament\Schemas\Schema;
use Filament\Support\Icons\Heroicon;
use Filament\Tables\Table;

/**
 * The ways readers can reach this library.
 *
 * Every row is scoped by row-level security, so this resource never filters by
 * tenant and never could: outside the request's scope the table reads empty,
 * and inside it there is nothing but this library's rows to list.
 */
class ChannelBindingResource extends Resource
{
    protected static ?string $model = ChannelBinding::class;

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedRectangleStack;

    public static function form(Schema $schema): Schema
    {
        return ChannelBindingForm::configure($schema);
    }

    public static function table(Table $table): Table
    {
        return ChannelBindingsTable::configure($table);
    }

    public static function getRelations(): array
    {
        return [
            //
        ];
    }

    public static function getModelLabel(): string
    {
        return __('portal.channel.label');
    }

    public static function getPluralModelLabel(): string
    {
        return __('portal.channel.label_plural');
    }

    public static function getNavigationLabel(): string
    {
        return __('portal.channel.label_plural');
    }

    /** Bindings come from provisioning; see ChannelBindingsTable. */
    public static function canCreate(): bool
    {
        return false;
    }

    public static function getPages(): array
    {
        return [
            'index' => ListChannelBindings::route('/'),
            'edit' => EditChannelBinding::route('/{record}/edit'),
        ];
    }
}
