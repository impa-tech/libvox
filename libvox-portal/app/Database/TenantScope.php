<?php

namespace App\Database;

use Closure;
use Illuminate\Database\ConnectionInterface;
use LogicException;

/**
 * Binds work to exactly one library, so row-level security can enforce it.
 *
 * The counterpart of `libvox_sync.db.tenant_scope`, but not a copy of it, and
 * the difference is worth explaining because the copy was tried first and does
 * not work here.
 *
 * Python binds with `set_config(..., true)` -- transaction-local, the
 * equivalent of SET LOCAL -- which is the stronger choice: the binding dies
 * with the transaction and cannot outlive it on a pooled connection. That
 * requires the binding to *wrap* the work, and in a Laravel + Livewire request
 * there is no single call to wrap.
 *
 * Livewire's update route carries no panel middleware. Filament's answer is
 * persistent middleware, which Livewire re-applies by running it through a
 * pipeline whose destination is `fn () => new Response()`. A middleware that
 * binds by wrapping `$next` therefore opens its scope, receives an empty
 * response immediately, and releases the binding before the component it was
 * meant to protect ever runs. Every write on the library panel was refused.
 *
 * So the binding is session-level: a side effect that persists on the
 * connection rather than a wrapper around a call. It is released when the
 * request terminates, re-applied if the connection is re-established
 * mid-request, and overwritten by the next request that binds.
 *
 * What that costs is the guarantee the transaction-local form gave for free:
 * this is safe because a connection belongs to one request. Persistent PDO
 * connections, Octane, and pgbouncer in transaction pooling mode each break
 * that, and each is named in the README as something this module must not grow.
 *
 * Outside a binding, `current_setting('libvox.tenant_id', true)` is NULL, every
 * policy matches nothing, and the tables read empty. **Forgetting to bind
 * yields no rows -- never another library's rows.**
 */
class TenantScope
{
    private ?string $current = null;

    /** True while `apply` is mid-query; see the guard there. */
    private bool $applying = false;

    public function __construct(private readonly ConnectionInterface $connection) {}

    /**
     * Bind this connection to one library, for the rest of the request.
     */
    public function bind(string $tenantId): void
    {
        $this->current = $tenantId;
        $this->apply();
    }

    /**
     * Unbind, fail-closed.
     *
     * The empty string rather than NULL: `set_config` cannot unset, and ''
     * matches no tenant_id, so an unbound connection still reads empty.
     */
    public function release(): void
    {
        $this->current = null;

        $this->connection->select("select set_config('libvox.tenant_id', '', false)");
    }

    /**
     * Re-apply the current binding.
     *
     * A session setting dies with the connection, so a reconnect mid-request --
     * after a lost connection, say -- would silently drop it and the request
     * would carry on reading empty. AppServiceProvider calls this on
     * ConnectionEstablished.
     */
    public function apply(): void
    {
        // Re-entrancy guard, and it is load-bearing rather than defensive.
        //
        // The select below runs a query. On a connection that has died --
        // a Discovery Index that went away mid-request, say -- Laravel
        // reconnects to run it, reconnecting fires ConnectionEstablished,
        // AppServiceProvider listens for that and calls this method, and the
        // whole thing recurses until PHP dies. Measured: 1,102 stack frames
        // and "Premature end of PHP process".
        if ($this->applying) {
            return;
        }

        if ($this->current !== null) {
            // select, not statement: set_config is a function, and this is the
            // form that reliably takes a binding under PDO.
            $this->applying = true;

            try {
                $this->connection->select(
                    "select set_config('libvox.tenant_id', ?, false)",
                    [$this->current],
                );
            } finally {
                $this->applying = false;
            }
        }
    }

    /**
     * Run $work bound to one library, in one transaction, and restore whatever
     * binding was in force before.
     *
     * For code that scopes explicitly rather than through the request: the
     * provisioner, the credential store, a console command, a test. The
     * transaction is what makes provisioning atomic; the restore is what lets
     * it be called from inside a request that is already bound.
     *
     * @template T
     *
     * @param  Closure(): T  $work
     * @return T
     */
    public function run(string $tenantId, Closure $work): mixed
    {
        $previous = $this->current;

        // Re-binding to a *different* library inside an open one is well
        // defined now -- the finally below restores it -- and still a bug in
        // the caller. Code that scopes to B while A is in force is confused
        // about which library it is acting for, and the restore would hide
        // that rather than the exception naming it.
        if ($previous !== null && $previous !== $tenantId) {
            throw new LogicException(
                "already scoped to {$previous}; cannot re-bind to {$tenantId}"
            );
        }

        return $this->connection->transaction(function () use ($tenantId, $work, $previous) {
            try {
                $this->bind($tenantId);

                return $work();
            } finally {
                $previous === null ? $this->release() : $this->bind($previous);
            }
        });
    }

    /** The library currently bound, if any. */
    public function current(): ?string
    {
        return $this->current;
    }
}
