<?php

namespace App\Tenancy;

use App\Channels\WidgetKey;
use App\Database\TenantScope;
use App\Enums\ChannelType;
use App\Models\ChannelBinding;
use App\Models\Tenant;
use App\Models\TenantWidget;

/**
 * Bringing a library into existence.
 *
 * Explicit rather than a model observer, so the order in which a library comes
 * into being is readable in one place: the registry row first, because
 * everything else references it, then the rows that make the library usable
 * rather than merely registered.
 *
 * The whole thing runs inside one tenant scope, which is also one transaction.
 * Not a transaction with a scope nested inside it: set_config(..., true) issued
 * within a savepoint applies for the remainder of the *enclosing* transaction,
 * not just the savepoint, so a nested scope would silently leave the binding in
 * place for whatever the outer transaction did next. One scope, one
 * transaction, no ambiguity about what is bound when.
 *
 * A library that exists but has no widget settings would render a form with
 * nothing behind it, which reads as a bug rather than as missing setup -- hence
 * both rows or neither.
 */
class TenantProvisioner
{
    public function __construct(private readonly TenantScope $scope) {}

    /**
     * @param  array<string, mixed>  $attributes
     */
    public function provision(array $attributes): Tenant
    {
        $tenantId = $attributes['tenant_id'];

        return $this->scope->run($tenantId, function () use ($attributes) {
            // tenants has no row-level security -- it is the registry that
            // defines the tenants -- so creating it inside a scope costs
            // nothing. tenant_widget does, and this is what lets it be written.
            $tenant = Tenant::create($attributes);

            TenantWidget::create(['tenant_id' => $tenant->tenant_id]);

            /*
             * A new library starts with the web widget and nothing else: it is
             * the only channel needing no account from a third party. Telegram,
             * WhatsApp and e-mail stay off until the library brings the
             * credential only it can create.
             *
             * It is born switched off, with an empty allowlist, and the
             * database agrees: a web binding cannot be enabled without at least
             * one origin, because a widget with no registered origin is
             * embeddable from any page on the internet.
             *
             * Nobody here knows the address to put in it. This used to be
             * seeded from an OPAC URL the operator typed on the registration
             * form, which was a guess by the one person in the arrangement who
             * does not run the OPAC -- and a guess that could never be
             * corrected, because editing a library did not go through this
             * code. The librarian registers it on her own Assistant page, where
             * she is already standing when she copies the snippet.
             */
            ChannelBinding::create([
                'tenant_id' => $tenant->tenant_id,
                'channel' => ChannelType::Web,
                'binding_key' => WidgetKey::mint(),
                'allowed_origins' => [],
                'enabled' => false,
            ]);

            return $tenant;
        });
    }
}
