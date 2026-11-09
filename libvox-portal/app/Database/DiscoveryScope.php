<?php

namespace App\Database;

/**
 * The tenant binding for the Discovery Index connection.
 *
 * A subclass and not a second instance of TenantScope, because Laravel's
 * container resolves singletons by class name and two bindings of one class
 * cannot be told apart. The behaviour is inherited whole: the difference is
 * only which connection it binds.
 *
 * It matters as much as the Tenant DB's. `discovery_biblio` carries the same
 * row-level security, so an unbound connection reads a library's catalogue as
 * empty -- and a *privileged* one reads every library's. See
 * AppServiceProvider, which refuses to serve on either.
 */
class DiscoveryScope extends TenantScope {}
