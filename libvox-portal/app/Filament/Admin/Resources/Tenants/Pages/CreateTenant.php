<?php

namespace App\Filament\Admin\Resources\Tenants\Pages;

use App\Filament\Admin\Resources\Tenants\TenantResource;
use App\Tenancy\TenantProvisioner;
use Filament\Resources\Pages\CreateRecord;
use Illuminate\Database\Eloquent\Model;

class CreateTenant extends CreateRecord
{
    protected static string $resource = TenantResource::class;

    /**
     * Registering a library is more than one row.
     *
     * Handed to TenantProvisioner rather than done here, so that a library
     * created by a seeder, a console command or a future onboarding flow comes
     * into existence the same way as one created from this form.
     *
     * @param  array<string, mixed>  $data
     */
    protected function handleRecordCreation(array $data): Model
    {
        return app(TenantProvisioner::class)->provision($data);
    }
}
