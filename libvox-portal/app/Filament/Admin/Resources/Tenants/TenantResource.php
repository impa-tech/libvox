<?php

namespace App\Filament\Admin\Resources\Tenants;

use App\Filament\Admin\Resources\Tenants\Pages\CreateTenant;
use App\Filament\Admin\Resources\Tenants\Pages\EditTenant;
use App\Filament\Admin\Resources\Tenants\Pages\ListTenants;
use App\Filament\Admin\Resources\Tenants\Schemas\TenantForm;
use App\Filament\Admin\Resources\Tenants\Tables\TenantsTable;
use App\Models\Tenant;
use BackedEnum;
use Filament\Resources\Resource;
use Filament\Schemas\Schema;
use Filament\Support\Icons\Heroicon;
use Filament\Tables\Table;

/**
 * The registry of libraries, on the operator's panel.
 *
 * Everything here reads tenants, which has no row-level security because it is
 * the table that defines which tenants exist. Nothing here reads a library's
 * credentials, catalog configuration or widget settings across libraries --
 * those tables are unreadable across tenants on purpose, and the operator
 * reaches them one library at a time, inside a scope, like everybody else.
 */
class TenantResource extends Resource
{
    protected static ?string $model = Tenant::class;

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedBuildingLibrary;

    protected static ?string $recordTitleAttribute = 'display_name';

    public static function getModelLabel(): string
    {
        return __('portal.tenant.label');
    }

    public static function getPluralModelLabel(): string
    {
        return __('portal.tenant.label_plural');
    }

    public static function getNavigationLabel(): string
    {
        return __('portal.tenant.label_plural');
    }

    public static function form(Schema $schema): Schema
    {
        return TenantForm::configure($schema);
    }

    public static function table(Table $table): Table
    {
        return TenantsTable::configure($table);
    }

    public static function getRelations(): array
    {
        return [
            //
        ];
    }

    public static function getPages(): array
    {
        return [
            'index' => ListTenants::route('/'),
            'create' => CreateTenant::route('/create'),
            'edit' => EditTenant::route('/{record}/edit'),
        ];
    }
}
