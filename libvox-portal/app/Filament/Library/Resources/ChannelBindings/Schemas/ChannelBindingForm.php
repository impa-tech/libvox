<?php

namespace App\Filament\Library\Resources\ChannelBindings\Schemas;

use App\Channels\Origin;
use App\Enums\ChannelType;
use Filament\Forms\Components\TagsInput;
use Filament\Forms\Components\TextInput;
use Filament\Forms\Components\Toggle;
use Filament\Schemas\Components\Section;
use Filament\Schemas\Schema;

class ChannelBindingForm
{
    public static function configure(Schema $schema): Schema
    {
        return $schema
            ->components([
                Section::make(__('portal.channel.section.identity'))
                    ->description(fn ($record): ?string => $record?->channel->credentialHint())
                    ->schema([
                        TextInput::make('binding_key')
                            ->label(fn ($record): string => match ($record?->channel) {
                                ChannelType::Web => __('portal.channel.widget_key'),
                                default => __('portal.channel.binding_key'),
                            })
                            ->helperText(__('portal.channel.binding_key_help'))
                            // Generated, not typed. Changing it invalidates every
                            // embed snippet already deployed, so it is done by a
                            // deliberate action with a confirmation, never by
                            // editing a field.
                            ->disabled()
                            ->dehydrated(false),
                    ]),

                Section::make(__('portal.channel.section.origins'))
                    ->description(__('portal.channel.section.origins_help'))
                    ->visible(fn ($record): bool => $record?->channel->usesOrigins() ?? false)
                    ->schema([
                        TagsInput::make('allowed_origins')
                            ->label(__('portal.channel.allowed_origins'))
                            ->helperText(__('portal.channel.allowed_origins_help'))
                            ->placeholder('https://opac.example.org')
                            // Normalized on the way in, so the allowlist holds
                            // one row per origin rather than four spellings of
                            // the same one. Anything unparseable is dropped here
                            // and refused by the database if it somehow is not.
                            ->dehydrateStateUsing(fn (?array $state): array => array_values(array_unique(
                                array_filter(array_map(Origin::normalize(...), $state ?? []))
                            ))),
                    ]),

                Section::make(__('portal.channel.section.state'))
                    ->schema([
                        Toggle::make('enabled')
                            ->label(__('portal.channel.enabled'))
                            ->helperText(__('portal.channel.enabled_help')),
                    ]),
            ]);
    }
}
