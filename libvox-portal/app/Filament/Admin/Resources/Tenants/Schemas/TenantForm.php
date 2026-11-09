<?php

namespace App\Filament\Admin\Resources\Tenants\Schemas;

use App\Enums\TenantStatus;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\TextInput;
use Filament\Schemas\Components\Section;
use Filament\Schemas\Schema;

class TenantForm
{
    public static function configure(Schema $schema): Schema
    {
        return $schema
            ->components([
                Section::make(__('portal.tenant.section.identity'))
                    ->description(__('portal.tenant.section.identity_help'))
                    ->schema([
                        TextInput::make('tenant_id')
                            ->label(__('portal.tenant.tenant_id'))
                            ->helperText(__('portal.tenant.tenant_id_help'))
                            ->required()
                            ->maxLength(35)
                            ->default('lib-')
                            // The database refuses a rename with a trigger, and
                            // this is the same fact said earlier: rows in the
                            // Discovery Index reference this string from another
                            // database, where no foreign key could catch it.
                            ->disabledOn('edit')
                            ->dehydratedWhenHidden()
                            ->rule('regex:/^lib-[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?$/'),

                        TextInput::make('display_name')
                            ->label(__('portal.tenant.display_name'))
                            ->required()
                            ->maxLength(120),
                    ]),

                Section::make(__('portal.tenant.section.service'))
                    ->schema([
                        Select::make('status')
                            ->label(__('portal.tenant.status_label'))
                            ->options(collect(TenantStatus::cases())
                                ->mapWithKeys(fn (TenantStatus $s) => [$s->value => $s->label()])
                                ->all())
                            ->default(TenantStatus::Provisioning->value)
                            ->required(),

                        Select::make('locale')
                            ->label(__('portal.tenant.locale'))
                            ->options(['pt-BR' => 'Português (Brasil)', 'en' => 'English'])
                            ->default('pt-BR')
                            ->required(),

                        TextInput::make('contact_email')
                            ->label(__('portal.tenant.contact_email'))
                            ->email()
                            ->maxLength(255),
                    ]),
            ]);
    }
}
