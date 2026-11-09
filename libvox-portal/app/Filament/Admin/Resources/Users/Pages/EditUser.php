<?php

namespace App\Filament\Admin\Resources\Users\Pages;

use App\Filament\Admin\Resources\Users\UserResource;
use Filament\Actions\DeleteAction;
use Filament\Resources\Pages\EditRecord;

class EditUser extends EditRecord
{
    protected static string $resource = UserResource::class;

    /**
     * The role, applied after the account is saved.
     *
     * syncRoles rather than assignRole: an account has exactly one role here,
     * and assignRole would add a second alongside whatever was there before --
     * which canAccessPanel would then answer for twice.
     *
     * Read from $this->data rather than the saved payload, because the field is
     * not a column on users and so never reaches it.
     */
    protected function afterSave(): void
    {
        $this->record->syncRoles([$this->data['role']]);
    }

    protected function getHeaderActions(): array
    {
        return [
            DeleteAction::make(),
        ];
    }
}
