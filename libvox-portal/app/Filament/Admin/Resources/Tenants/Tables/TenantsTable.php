<?php

namespace App\Filament\Admin\Resources\Tenants\Tables;

use App\Enums\TenantStatus;
use Filament\Actions\EditAction;
use Filament\Tables\Columns\IconColumn;
use Filament\Tables\Columns\TextColumn;
use Filament\Tables\Filters\SelectFilter;
use Filament\Tables\Table;

class TenantsTable
{
    public static function configure(Table $table): Table
    {
        return $table
            ->columns([
                TextColumn::make('tenant_id')
                    ->label(__('portal.tenant.tenant_id'))
                    ->searchable()
                    ->sortable()
                    ->copyable(),

                TextColumn::make('display_name')
                    ->label(__('portal.tenant.display_name'))
                    ->searchable(),

                TextColumn::make('status')
                    ->label(__('portal.tenant.status_label'))
                    ->badge()
                    ->formatStateUsing(fn (TenantStatus $state) => $state->label())
                    ->color(fn (TenantStatus $state) => match ($state) {
                        TenantStatus::Active => 'success',
                        TenantStatus::Provisioning => 'warning',
                        TenantStatus::Suspended => 'danger',
                    }),

                /*
                 * Read from tenants, not from tenant_ils.
                 *
                 * tenant_ils is unreadable across libraries by design, and this
                 * is the one question the operator asks across all of them.
                 * Answering it from a column on the registry keeps the operator
                 * panel free of any cross-tenant read at all -- which is why
                 * there is no privileged connection and no SECURITY DEFINER
                 * function anywhere in this module.
                 */
                IconColumn::make('ils_configured_at')
                    ->label(__('portal.tenant.ils_configured'))
                    ->boolean()
                    ->getStateUsing(fn ($record): bool => $record->ils_configured_at !== null),

                TextColumn::make('ils_configured_at')
                    ->label(__('portal.tenant.ils_configured_at'))
                    ->dateTime()
                    ->placeholder(__('portal.tenant.never'))
                    ->toggleable(isToggledHiddenByDefault: true),

                TextColumn::make('created_at')
                    ->label(__('portal.tenant.created_at'))
                    ->dateTime()
                    ->sortable()
                    ->toggleable(isToggledHiddenByDefault: true),
            ])
            ->filters([
                SelectFilter::make('status')
                    ->label(__('portal.tenant.status_label'))
                    ->options(collect(TenantStatus::cases())
                        ->mapWithKeys(fn (TenantStatus $s) => [$s->value => $s->label()])
                        ->all()),
            ])
            ->recordActions([
                EditAction::make(),
            ])
            // No bulk delete. Deleting a library cascades to its credentials,
            // its widget settings and its librarians' accounts, and a checkbox
            // column is the wrong affordance for that.
            ->toolbarActions([]);
    }
}
