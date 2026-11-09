<?php

namespace App\Filament\Admin\Resources\Users\Schemas;

use App\Enums\UserRole;
use App\Models\Tenant;
use App\Models\User;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\TextInput;
use Filament\Schemas\Components\Utilities\Get;
use Filament\Schemas\Schema;
use Illuminate\Support\Facades\Hash;

class UserForm
{
    public static function configure(Schema $schema): Schema
    {
        return $schema
            ->components([
                TextInput::make('name')
                    ->label(__('portal.user.name'))
                    ->required()
                    ->maxLength(120),

                TextInput::make('email')
                    ->label(__('portal.user.email'))
                    ->email()
                    ->required()
                    ->unique(ignoreRecord: true)
                    ->maxLength(255),

                /*
                 * One role, not a list.
                 *
                 * Shield stores roles as a many-to-many, and binding this field
                 * to that relationship would make its value a set of role ids.
                 * Two things go wrong with that. The obvious one is the bug
                 * this replaces: options keyed by role *name* against a
                 * relationship that saves by *id*, which reaches Postgres as
                 * `where roles.id = 'librarian'`.
                 *
                 * The one that matters more is that the domain has no room for
                 * a set. An account is an operator or a librarian, never both:
                 * canAccessPanel asks which, tenant_id is required for one and
                 * meaningless for the other. A multi-select would let a form
                 * express a state nothing downstream can act on.
                 *
                 * So the field is the role name, the storage detail stays in
                 * the page's syncRoles, and the value here is the same string
                 * the enum and canAccessPanel use.
                 */
                Select::make('role')
                    ->label(__('portal.user.role'))
                    ->options(collect(UserRole::cases())
                        ->mapWithKeys(fn (UserRole $r) => [$r->value => $r->label()])
                        ->all())
                    ->afterStateHydrated(fn (Select $component, ?User $record) => $component->state(
                        $record?->roles->first()?->name
                    ))
                    // Not a column on users: the page assigns it after saving.
                    ->dehydrated(false)
                    ->required()
                    ->live(),

                Select::make('tenant_id')
                    ->label(__('portal.user.tenant'))
                    ->helperText(__('portal.user.tenant_help'))
                    ->options(fn () => Tenant::orderBy('display_name')->pluck('display_name', 'tenant_id'))
                    ->searchable()
                    // An operator belongs to LibVox and has no library. A
                    // librarian without one is refused the panel outright, so
                    // the field is required exactly when it is meaningful.
                    ->visible(fn (Get $get): bool => $get('role') === UserRole::Librarian->value)
                    ->required(fn (Get $get): bool => $get('role') === UserRole::Librarian->value)
                    // Filament does not dehydrate a hidden field, so without
                    // this a librarian promoted to operator would keep a
                    // tenant_id that the form no longer shows -- invisible in
                    // the panel and still in the column.
                    ->dehydratedWhenHidden()
                    // An operator has no library, so a role change must clear a
                    // tenant left behind by the previous selection rather than
                    // save it invisibly.
                    ->dehydrateStateUsing(fn (?string $state, Get $get): ?string => $get('role') === UserRole::Librarian->value ? $state : null),

                TextInput::make('password')
                    ->label(__('portal.user.password'))
                    ->password()
                    ->revealable(false)
                    ->dehydrateStateUsing(fn (?string $state) => filled($state) ? Hash::make($state) : null)
                    ->dehydrated(fn (?string $state): bool => filled($state))
                    ->required(fn (string $operation): bool => $operation === 'create')
                    ->helperText(__('portal.user.password_help'))
                    ->maxLength(255),
            ]);
    }
}
