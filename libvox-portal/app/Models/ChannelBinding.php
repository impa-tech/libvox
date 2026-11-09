<?php

namespace App\Models;

use App\Enums\ChannelType;
use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * One way a reader reaches one library.
 *
 * Reachable only inside a tenant scope: the table carries row-level security,
 * so outside one it reads empty. The secret column is hidden and unfillable --
 * a channel secret is written through ChannelSecretStore, for the same reason
 * an ILS secret is.
 */
class ChannelBinding extends Model
{
    protected $fillable = [
        'tenant_id',
        'channel',
        'binding_key',
        'enabled',
        'allowed_origins',
        'metadata',
    ];

    /** Never in an array, a JSON body, a Livewire snapshot or a table column. */
    protected $hidden = ['secret_enc'];

    protected $guarded = ['secret_enc'];

    protected function casts(): array
    {
        return [
            'channel' => ChannelType::class,
            'enabled' => 'boolean',
            'allowed_origins' => 'array',
            'metadata' => 'array',
        ];
    }

    public function tenant(): BelongsTo
    {
        return $this->belongsTo(Tenant::class, 'tenant_id', 'tenant_id');
    }
}
