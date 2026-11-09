<?php

namespace App\Filament\Admin\Resources\Users\Tables;

use Filament\Actions\DeleteAction;
use Filament\Actions\EditAction;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Table;

class UsersTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('name')->label(__('portal.user.name'))->searchable(),
                TextColumn::make('email')->label(__('portal.user.email'))->searchable(),
                TextColumn::make('roles.name')
                    ->label(__('portal.user.role'))
                    ->badge(),
                TextColumn::make('tenant.display_name')
                    ->label(__('portal.user.tenant'))
                    ->placeholder(__('portal.user.no_tenant')),
            ])
            ->recordActions([
                EditAction::make(),
                DeleteAction::make(),
            ])
            ->toolbarActions([]);
    }
}
