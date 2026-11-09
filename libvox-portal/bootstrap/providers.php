<?php

use App\Providers\AppServiceProvider;
use App\Providers\Filament\AdminPanelProvider;
use App\Providers\Filament\LibraryPanelProvider;

return [
    AppServiceProvider::class,
    AdminPanelProvider::class,
    LibraryPanelProvider::class,
];
