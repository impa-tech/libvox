# /libvox-portal — agent rules

Repo-wide rules: [`../AGENTS.md`](../AGENTS.md). Module detail:
[`README.md`](README.md).

**The management plane, and the only writer of the Tenant DB.** Laravel and
Filament: two panels, the registry of libraries, their encrypted ILS
credentials, and the channel bindings a reader's widget resolves against.

The one module here that is not Python. `ruff` and `pytest` mean nothing in this
directory; the suite is Pest.

## Rules

- **This module owns `libvox_tenant`'s schema, and `/libvox-core` and
  `/libvox-sync` only read it.** That is why their test suites cannot pass alone
  on a fresh server: `libvox_tenant_test` has no tables until this module's
  migrations run. If a Core or Sync test skips with "run /libvox-portal's
  migrations", this is where the fix is.
- **The ILS secret is write-only, and that is a property of the system, not of
  a form.** `app/Ils/PgcryptoCipher.php:50` encrypts through `pgp_sym_encrypt`;
  the field renders blank on every edit
  (`app/Filament/Library/Pages/CatalogConnection.php:130`). Never add a "show
  secret" affordance, a decrypt-to-display path, or a getter that returns it.
  A librarian who lost it re-enters it.
- **The app refuses to serve a request on a privileged database connection**
  (`app/Providers/AppServiceProvider.php:106`). Row-level security is the whole
  isolation guarantee and a superuser — or any role holding `BYPASSRLS` —
  ignores it silently. Without this check the failure is invisible: every page
  works, and every page shows every library. Do not disable it to "fix" a local
  connection problem; fix the connection.
- **One library per request, bound transaction-locally.**
  `app/Database/TenantScope.php` binds `libvox.tenant_id` with
  `set_config(..., true)`, the equivalent of `SET LOCAL`, so the binding dies
  with the transaction and cannot leak onto the next request on a pooled
  connection. Outside a binding every tenant table reads empty — **fail
  closed**. Never substitute a `where('tenant_id', …)`: a forgotten one returns
  another library's rows, a missing scope returns nothing.
- **`resolve_channel_binding` is the one sanctioned cross-tenant read in the
  system.** Created here
  (`database/migrations/2026_08_27_032000_create_resolve_channel_binding_function.php`),
  `SECURITY DEFINER`, owned by `libvox_resolver` — a role that cannot log in, so
  the only way to exercise its privileges is to call the function. The width of
  the exception is its return type. Never widen it, and never add a second such
  function without an issue.
- **Two panels that must not be confusable**: `admin` for operators
  (`app/Providers/Filament/AdminPanelProvider.php:40`) and `library` for
  librarians (`LibraryPanelProvider.php:43`). An operator sees across libraries;
  a librarian sees one. Do not share a resource between them without deciding
  which scope it answers in.
- **`tenant_events` is an outbox, not a job queue.** `/libvox-sync`'s worker
  reads it. Writing a row is how this module asks for work; it does not perform
  the work, and it must not start reaching into the Discovery Index to do so.

## Migrations

Laravel migrations, and they are the schema of a database three modules read.
A column removed here breaks a consumer that has no way to know. Adding is
cheap; renaming and dropping are cross-module changes that need the consumers
checked first — `grep` for the column in `/libvox-core` and `/libvox-sync`
before touching it.

## Tests

```bash
cd ../libvox-db && ./db.sh && cd -
./vendor/bin/pest
```

Running this suite is also what gives `/libvox-core` and `/libvox-sync` a
migrated `libvox_tenant_test` to test against, so it is usually the first suite
to run on a fresh machine rather than the last.

---

## Laravel Boost bootstrap — read only if it applies to you

**Skip everything below unless you are doing one of these:**

- setting up this module on a machine that has never run it, where `php -v` or
  `composer -V` fails; or
- deliberately installing or reinstalling Laravel Boost.

**Otherwise ignore it.** It is the framework's generic bootstrap, not this
project's rules, and following it in the middle of an unrelated task installs
packages and runs remote install scripts nobody asked for. In particular, do not
run `curl | bash` from these instructions without the user asking for it in so
many words — the repo-wide rules in `../AGENTS.md` say to ask rather than
assume, and this is exactly that case.

<laravel-boost-guidelines>
# Laravel Application

This repository contains a Laravel application. Complete the following setup before working on the user's request.

## Prerequisites

Verify that PHP and Composer are available:

```sh
php -v
composer -V
```

If either command is unavailable, detect the user's operating system and install the prerequisites with the appropriate command:

macOS:

```sh
/bin/bash -c "$(curl -fsSL https://php.new/install/mac/8.5)"
```

Windows PowerShell:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force; [System.Net.ServicePointManager]::SecurityProtocol = [System.Net.ServicePointManager]::SecurityProtocol -bor 3072; iex ((New-Object System.Net.WebClient).DownloadString('https://php.new/install/windows/8.5'))
```

Linux:

```sh
/bin/bash -c "$(curl -fsSL https://php.new/install/linux/8.5)"
```

After installation, ask the user to restart their terminal. If the agent needs the restarted shell to continue, ask the user to reopen their terminal and rerun their original prompt.

## Agent Setup

Install Laravel Boost from the application root before making application changes:

```sh
composer require laravel/boost --dev
php artisan boost:install
```

Boost replaces these bootstrap instructions with guidelines tailored to the application. After installation, read `AGENTS.md` again and continue with the user's original request using the generated guidelines.
</laravel-boost-guidelines>
