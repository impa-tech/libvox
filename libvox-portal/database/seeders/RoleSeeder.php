<?php

namespace Database\Seeders;

use App\Enums\UserRole;
use Illuminate\Database\Seeder;
use Spatie\Permission\Models\Role;

/**
 * The two roles the panels gate on.
 *
 * They exist as data because Shield manages permissions as data, and as an
 * enum because a typo in a role name is a typo in what a panel lets through.
 * This seeder is the one place the two are reconciled.
 */
class RoleSeeder extends Seeder
{
    public function run(): void
    {
        foreach (UserRole::cases() as $role) {
            Role::findOrCreate($role->value, 'web');
        }
    }
}
