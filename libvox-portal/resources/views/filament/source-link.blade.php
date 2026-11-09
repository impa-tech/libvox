{{--
    The offer of source, at the foot of every screen in both panels.

    LibVox is AGPL-3.0, and §13 asks a *modified* version offered to people over
    a network to give them a way to obtain the corresponding source. A librarian
    working in this panel is one of those people; the reader talking to the
    widget is the other, and /libvox-core shows them the same link.

    Rendered unconditionally, unlike the status bar it sits under -- that one is
    about the signed-in librarian, and this is about the software.

    The URL is configuration, not a constant: an installation that patched a
    file owes its users *its* source. Empty removes the notice entirely, which
    is the deployment holding a commercial licence.
--}}
@php
    $source = config('libvox.source_url');
@endphp

@if (filled($source))
    <div class="px-4 pb-4 text-center text-xs text-gray-400 sm:px-6 lg:px-8 dark:text-gray-500">
        {{ __('portal.source.notice') }}
        <a
            href="{{ $source }}"
            target="_blank"
            rel="noopener noreferrer"
            class="font-medium underline underline-offset-2 hover:text-gray-600 dark:hover:text-gray-300"
        >{{ __('portal.source.link') }}</a>
    </div>
@endif
