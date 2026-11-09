<?php

namespace App\Filament\Admin\Resources\Users\Pages;

use App\Filament\Admin\Resources\Users\UserResource;
use Filament\Resources\Pages\CreateRecord;

class CreateUser extends CreateRecord
{
    protected static string $resource = UserResource::class;

    /**
     * The role, applied after the account exists.
     *
     * syncRoles rather than assignRole: an account has exactly one role here,
     * and assignRole would add a second alongside whatever was there before --
     * which canAccessPanel would then answer for twice.
     */
    protected function afterCreate(): void
    {
        $this->record->syncRoles([$this->data['role']]);
    }
}
