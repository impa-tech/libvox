<?php

namespace App\Filament\Library\Resources\ChannelBindings\Pages;

use App\Filament\Library\Resources\ChannelBindings\ChannelBindingResource;
use Filament\Actions\CreateAction;
use Filament\Resources\Pages\ListRecords;

class ListChannelBindings extends ListRecords
{
    protected static string $resource = ChannelBindingResource::class;

    protected function getHeaderActions(): array
    {
        return [
            CreateAction::make(),
        ];
    }
}
