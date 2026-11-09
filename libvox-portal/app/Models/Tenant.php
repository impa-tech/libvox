<?php

namespace App\Models;

use App\Enums\TenantStatus;
use Illuminate\Database\Eloquent\Model;

/**
 * One library.
 *
 * The primary key is the tenant_id itself -- the same string that reaches
 * set_config('libvox.tenant_id', ...) and that every row-level security policy
 * in this database compares against. It is immutable, enforced by a trigger
 * rather than by a disabled form field, because rows in the Discovery Index
 * reference it from another database where no foreign key could catch a rename.
 */
class Tenant extends Model
{
    protected $primaryKey = 'tenant_id';

    protected $keyType = 'string';

    public $incrementing = false;

    protected $fillable = [
        'tenant_id',
        'display_name',
        'status',
        'locale',
        'contact_email',
    ];

    protected function casts(): array
    {
        return [
            'status' => TenantStatus::class,
        ];
    }

    public function getRouteKeyName(): string
    {
        return 'tenant_id';
    }
}
