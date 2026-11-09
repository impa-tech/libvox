{{--
    The bar under every screen in the librarian's panel: who is signed in, and
    which library this session is bound to.

    The second half is the one worth having. A librarian belongs to exactly one
    library and the tenant is never in the URL -- deliberately, because a tenant
    a browser can edit is a tenant an attacker can edit -- so until now nothing
    on screen said which library the page was about. That identifier is also
    what every other component of LibVox calls her library, so it is the string
    to quote in a support message.

    Rendered through PanelsRenderHook::FOOTER, which sits after <main> inside
    the content column: it scrolls with the page and never covers anything.
--}}
@php
    $user = \Filament\Facades\Filament::auth()->user();
@endphp

@if ($user?->tenant_id)
    <div
        class="mt-6 flex flex-wrap items-center justify-between gap-x-6 gap-y-1 border-t border-gray-200 px-4 py-3 text-xs text-gray-500 sm:px-6 lg:px-8 dark:border-white/10 dark:text-gray-400"
    >
        <span>
            {{ __('portal.status_bar.signed_in_as') }}
            <span class="font-medium text-gray-700 dark:text-gray-300">{{ $user->email }}</span>
        </span>

        <span>
            {{ __('portal.status_bar.library') }}
            <span class="font-mono font-medium text-gray-700 dark:text-gray-300">{{ $user->tenant_id }}</span>
        </span>
    </div>
@endif
