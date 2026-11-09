<?php

namespace App\Filament\Library\Resources\ChannelBindings\Tables;

use App\Enums\ChannelType;
use Filament\Actions\EditAction;
use Filament\Tables\Columns\IconColumn;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Table;

class ChannelBindingsTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('channel')
                    ->label(__('portal.channel.label'))
                    ->badge()
                    ->formatStateUsing(fn (ChannelType $state) => $state->label()),

                TextColumn::make('binding_key')
                    ->label(__('portal.channel.binding_key'))
                    ->copyable()
                    // Public by design -- it goes in the OPAC's page source.
                    // Shown in full because a librarian has to paste it.
                    ->limit(24),

                IconColumn::make('enabled')
                    ->label(__('portal.channel.enabled'))
                    ->boolean(),

                // One badge per origin, not a joined string.
                //
                // Filament calls formatStateUsing once per element when the
                // state is an array, which is why a closure typed ?array was
                // handed a string and threw. Letting it list them natively is
                // also the better reading: the allowlist is compared exactly,
                // and four spellings of one origin are four entries -- a
                // comma-joined line hides that, and separate badges do not.
                TextColumn::make('allowed_origins')
                    ->label(__('portal.channel.allowed_origins'))
                    ->badge()
                    ->listWithLineBreaks()
                    ->limitList(3)
                    ->expandableLimitedList()
                    ->placeholder(__('portal.channel.no_origins')),
            ])
            ->recordActions([
                EditAction::make(),
            ])
            // A binding is created by provisioning, not from this screen: every
            // channel but the web widget needs a credential the library has to
            // obtain first, and an empty row would be a channel that looks
            // configured and answers nothing.
            ->toolbarActions([]);
    }
}
