<?php

return [

    /*
     * Encrypts stored ILS client secrets. Passed to PostgreSQL as a bind
     * parameter on every statement, never as a session variable and never
     * written to the database it protects.
     *
     * /libvox-sync must be given the same value: it reads what this portal
     * writes, through the same pgcrypto functions.
     */
    'crypto_key' => env('LIBVOX_CRYPTO_KEY'),

    /*
     * Refuse to serve if the application's database connection turns out to be
     * privileged. A superuser -- or any role with BYPASSRLS -- ignores
     * row-level security unconditionally, which would leave every tenant
     * isolation policy in the schema enforcing nothing.
     *
     * There is no legitimate reason to turn this off. It exists as a switch
     * only so the check can be skipped where it would be circular.
     */
    'assert_unprivileged' => (bool) env('LIBVOX_ASSERT_UNPRIVILEGED', true),

    /*
     * Where /libvox-core publishes its public surface: the widget script and
     * the WebSocket behind it. Used to build the embed a library pastes into
     * its OPAC, and to load the widget for the preview on that page.
     *
     * The portal never serves the widget itself. It is the management plane;
     * putting it in the path of every OPAC page view would mean this
     * application going down takes every library's widget with it.
     */
    /*
     * /libvox-core's internal API, and the token that authenticates this
     * application to it.
     *
     * Used for one thing: asking whether a library's catalogue connection
     * works. This portal cannot answer that itself -- IlsCredentialStore has
     * no read path, and two tests hold that line -- so it asks the component
     * the architecture names as the one that may decrypt.
     *
     * The token says *which service is asking*, never which library. The
     * library travels in the request body, resolved here from the signed-in
     * user and never from input.
     */
    'core_url' => rtrim((string) env('LIBVOX_CORE_URL', 'http://127.0.0.1:8091'), '/'),
    'core_service_token' => env('LIBVOX_CORE_SERVICE_TOKEN'),

    /*
     * Where a reader's browser reaches /libvox-core.
     *
     * Deliberately without a default. It used to be http://127.0.0.1:8092,
     * which is correct on a laptop and wrong on every server -- 127.0.0.1 in a
     * reader's browser is the reader's own machine, so the snippet copied off
     * the Assistant page loaded nothing and said nothing about why.
     *
     * Left unset, WidgetSettings::widgetUrl() derives it from the request the
     * librarian is making, which is right whenever Core and the panel share a
     * host. Set it when they do not, or when a proxy terminates TLS in front of
     * one and not the other; an explicit value always wins.
     */
    'widget_url' => env('LIBVOX_WIDGET_URL'),

    /* The port half of that guess. Core publishes its public app on 8092. */
    'widget_port' => (int) env('LIBVOX_WIDGET_PORT', 8092),

    /*
     * Where a librarian is told the source lives, shown in the footer of both
     * panels.
     *
     * LibVox is AGPL-3.0, and §13 asks a *modified* version offered over a
     * network to give the people using it a way to obtain the corresponding
     * source. A librarian working in this panel is one of those people.
     *
     * Configurable because it has to be: an installation that patched a file
     * owes its users *its* source, not ours, and a hardcoded link would send
     * them to code nobody is running. The same variable configures
     * /libvox-core, which shows the same offer to readers in the widget.
     *
     * Set it to an empty string to drop the link, which is the deployment
     * holding a commercial licence rather than the AGPL.
     */
    'source_url' => trim((string) env(
        'LIBVOX_SOURCE_URL',
        'https://github.com/anselmobattisti/libvox',
    )),

];
