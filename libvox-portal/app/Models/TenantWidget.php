<?php

namespace App\Models;

use Illuminate\Database\Eloquent\Model;
use Illuminate\Database\Eloquent\Relations\BelongsTo;

/**
 * One library's persona and widget appearance.
 *
 * Reachable only inside a tenant scope: the table has row-level security, so
 * outside one it reads empty rather than raising. That is the intended
 * behaviour, and the reason the library panel binds every request.
 */
class TenantWidget extends Model
{
    protected $table = 'tenant_widget';

    protected $primaryKey = 'tenant_id';

    protected $keyType = 'string';

    public $incrementing = false;

    protected $fillable = [
        'tenant_id',
        'assistant_name',
        'welcome_message',
        'default_locale',
        'persona_tone',
        'theme_color',
    ];

    public function tenant(): BelongsTo
    {
        return $this->belongsTo(Tenant::class, 'tenant_id', 'tenant_id');
    }
}
