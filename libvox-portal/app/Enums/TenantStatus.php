<?php

namespace App\Enums;

/**
 * Paired with the CHECK constraint on tenants.status.
 *
 * Both exist on purpose: the enum is what the panels and the code read, and
 * the constraint is what holds when a row arrives from somewhere that is not
 * this application -- a seeder, a psql session, a data migration.
 */
enum TenantStatus: string
{
    /** Registered, but not yet answering: no ILS credentials, or no index. */
    case Provisioning = 'provisioning';

    /** Answering readers. The only status a channel binding resolves under. */
    case Active = 'active';

    /** Deliberately switched off, without deleting anything. */
    case Suspended = 'suspended';

    public function label(): string
    {
        return __('portal.tenant.status.'.$this->value);
    }
}
