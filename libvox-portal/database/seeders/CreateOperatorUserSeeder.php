<?php

namespace Database\Seeders;

use App\Enums\UserRole;
use App\Models\User;
use Illuminate\Database\Seeder;
use Illuminate\Support\Facades\Hash;

/**
 * The first operator, so /admin is reachable on a fresh install.
 *
 * The password comes from the environment and falls back to a value that is
 * obviously not a secret. There is no point pretending otherwise: a seeded
 * default password is a development convenience, and naming it as one is
 * better than a random string nobody can log in with.
 */
class CreateOperatorUserSeeder extends Seeder
{
    public function run(): void
    {
        $user = User::firstOrCreate(
            ['email' => env('LIBVOX_OPERATOR_EMAIL', 'operator@libvox.local')],
            [
                'name' => 'LibVox Operator',
                'password' => Hash::make(env('LIBVOX_OPERATOR_PASSWORD', 'password')),
                // An operator belongs to LibVox, not to a library.
                'tenant_id' => null,
            ],
        );

        $user->assignRole(UserRole::Operator->value);
    }
}
