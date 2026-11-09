<?php

namespace App\Models;

use App\Enums\UserRole;
use Filament\Models\Contracts\FilamentUser;
use Filament\Panel;
use Illuminate\Database\Eloquent\Relations\BelongsTo;
use Illuminate\Foundation\Auth\User as Authenticatable;
use Illuminate\Notifications\Notifiable;
use Spatie\Permission\Traits\HasRoles;

class User extends Authenticatable implements FilamentUser
{
    use HasRoles, Notifiable;

    protected $fillable = ['name', 'email', 'password', 'tenant_id', 'locale'];

    protected $hidden = ['password', 'remember_token'];

    protected function casts(): array
    {
        return [
            'email_verified_at' => 'datetime',
            'password' => 'hashed',
        ];
    }

    /** Null for an operator. Required for a librarian to reach anything. */
    public function tenant(): BelongsTo
    {
        return $this->belongsTo(Tenant::class, 'tenant_id', 'tenant_id');
    }

    public function isOperator(): bool
    {
        return $this->hasRole(UserRole::Operator->value);
    }

    public function isLibrarian(): bool
    {
        return $this->hasRole(UserRole::Librarian->value);
    }

    /**
     * Which panel this person may open.
     *
     * The two panels are separate applications as far as a signed-in user is
     * concerned: an operator never sees a library's configuration screens, and
     * a librarian never sees the registry of other libraries.
     *
     * This is authorization, and it is worth being clear about what it is not.
     * It does not keep one library's rows away from another library's request
     * -- row-level security does that, in the database, and it would still hold
     * if this method returned true for everyone. What this decides is which
     * screens exist for whom.
     *
     * A librarian with no tenant_id is refused outright rather than shown an
     * empty panel: without a library there is nothing for the request to be
     * scoped to, and an unscoped session reads every tenant table as empty.
     */
    public function canAccessPanel(Panel $panel): bool
    {
        return match ($panel->getId()) {
            'admin' => $this->isOperator(),
            'library' => $this->isLibrarian() && $this->tenant_id !== null,
            default => false,
        };
    }
}
