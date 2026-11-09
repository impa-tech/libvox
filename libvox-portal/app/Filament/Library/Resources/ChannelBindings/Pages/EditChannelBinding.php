<?php

namespace App\Filament\Library\Resources\ChannelBindings\Pages;

use App\Filament\Library\Resources\ChannelBindings\ChannelBindingResource;
use Filament\Actions\DeleteAction;
use Filament\Resources\Pages\EditRecord;

class EditChannelBinding extends EditRecord
{
    protected static string $resource = ChannelBindingResource::class;

    protected function getHeaderActions(): array
    {
        return [
            DeleteAction::make(),
        ];
    }
}
