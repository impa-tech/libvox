{{--
    The embed a library pastes, and a preview of what its readers will see.

    The snippet is generated, never transcribed: a librarian copying a key by
    hand can copy the wrong one, and a widget bound to another library is a
    failure that looks like a working install.

    The preview loads the real widget from /libvox-core and calls its `render`,
    which draws from a plain object and knows nothing about sockets. So this is
    the actual component, with the actual styles, fed from an unsaved form --
    not a mock-up that can drift from it.
--}}
@php
    $embed = $this->embed();
    $channel = $this->webChannel();
@endphp

<div class="space-y-4">
    @if ($embed === null)
        {{-- Two states with two different fixes, so two different messages.
             One sentence for both would send someone to add an address to a
             channel that was never created. --}}
        <x-filament::section.description>
            {{ __('portal.widget.no_channel') }}
        </x-filament::section.description>
    @else
        <div x-data="{ copied: false }" class="space-y-2">
            <div class="flex items-start gap-2">
                <pre class="flex-1 overflow-x-auto rounded-lg bg-gray-950/5 p-3 text-xs
                            leading-relaxed dark:bg-white/5"><code>{{ $embed }}</code></pre>

                <x-filament::button
                    size="sm"
                    x-on:click="navigator.clipboard.writeText(@js($embed)); copied = true;
                                setTimeout(() => copied = false, 2000)"
                >
                    <span x-show="! copied">{{ __('portal.widget.copy') }}</span>
                    <span x-show="copied" x-cloak>{{ __('portal.widget.copied') }}</span>
                </x-filament::button>
            </div>

            {{-- Not "has no allowed origin": the database refuses an enabled
                 web channel with none of those (channel_bindings_web_needs_an_origin).
                 So the state a library actually lands in is registered and
                 switched off, and that is what is worth saying. --}}
            @if (! $channel->enabled)
                <x-filament::section.description class="text-warning-600 dark:text-warning-400">
                    {{ __('portal.widget.channel_off') }}
                </x-filament::section.description>
            @endif
        </div>
    @endif

    <div
        x-data="{
            loaded: false,
            failed: false,
            draw() {
                if (! this.loaded || ! window.LibVox) return;
                window.LibVox.render($refs.preview, {
                    assistant_name: $wire.data.assistant_name,
                    welcome_message: $wire.data.welcome_message,
                    theme_color: $wire.data.theme_color,
                    locale: $wire.data.default_locale,
                });
            },
        }"
        x-init="
            {{-- Loaded from /libvox-core, the same origin that serves it to a
                 real OPAC, so the preview cannot drift from the thing itself.
                 If that host is down the page still shows the snippet, which
                 is correct regardless. --}}
            const script = document.createElement('script');
            script.src = @js($this->widgetUrl() . '/widget.js');
            script.onload = () => { loaded = true; draw(); };
            script.onerror = () => { failed = true; };
            document.head.appendChild(script);
        "
        {{-- x-effect re-runs whenever a value it read changes, which is how the
             preview follows the form without anything wiring each field to it
             by hand. --}}
        x-effect="draw()"
        class="space-y-2"
    >
        <div class="text-sm font-medium">{{ __('portal.widget.preview') }}</div>
        <div class="text-xs text-gray-500 dark:text-gray-400">
            {{ __('portal.widget.preview_help') }}
        </div>

        <div x-ref="preview" class="pt-1"></div>

        <div x-show="failed" x-cloak class="text-xs text-gray-500 dark:text-gray-400">
            {{ __('portal.widget.preview_unavailable') }}
        </div>
    </div>
</div>
