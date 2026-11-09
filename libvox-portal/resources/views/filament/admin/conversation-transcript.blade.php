{{--
    The conversation as it happened, with the machinery folded away.

    **The machinery was most of the screen.** A tool call and its result were two
    rows each, and across the whole log that is 784 rows of them against 423 of
    conversation -- 65%. The exchange a person came to read was buried in the
    thing that produced it.

    So a call and its result are one strip now, and the JSON is folded behind
    it. Nothing is hidden: the strip carries what a person scans for -- which
    tool, how many records, whether it failed -- and both halves in full are one
    click below it. This is still the screen that answers "why did she say
    that?", and the answer is what she was looking at.

    **It was a modal until it wasn't.** Reading a transcript is comparing calls,
    and a modal makes that a matter of memory: open, read, close, open the next,
    try to remember the first. The blocks are in the page now, and `abrir todos`
    puts every one of them on screen at once. Alpine only, no round trip -- the
    JSON is already in the document, highlighted server-side, and the toggles
    just decide what shows.

    Styled with utility classes, which is new. Until the theme was built they
    were inert and this file rendered as unstyled stacked divs -- no bubbles, no
    alignment, and answers with their line breaks collapsed.
--}}
<x-filament-panels::page>
    @php
        $answers = $this->toolResults();
        $blocks = $this->toolJson();
    @endphp

    {{-- `everything` is the default every strip falls back to; `opened` holds
         the ones a person has decided about since. Flipping the default clears
         those, so `abrir todos` means all of them and not all-but-the-three-I
         -closed-earlier. --}}
    <div x-data="{ everything: false, opened: {} }" class="space-y-4">
        <div class="flex items-start justify-between gap-4">
            <div class="text-sm text-gray-500 dark:text-gray-400">
                {{ __('portal.conversation.started') }}:
                {{ $this->record->started_at?->format('d/m/Y H:i') }}
                &nbsp;·&nbsp; {{ $this->record->channel }}
            </div>

            @if (filled($blocks))
                <button
                    type="button"
                    x-on:click="everything = ! everything; opened = {}"
                    x-text="everything
                        ? @js(__('portal.conversation.collapse_all'))
                        : @js(__('portal.conversation.expand_all'))"
                    class="shrink-0 text-xs font-medium text-primary-600 hover:underline
                           dark:text-primary-400"
                >{{ __('portal.conversation.expand_all') }}</button>
            @endif
        </div>

    <div class="space-y-3">
        @foreach ($this->record->messages as $message)
            @if ($message->role === 'user')
                <div class="flex justify-end">
                    <div class="max-w-2xl whitespace-pre-wrap rounded-xl rounded-br-sm
                                bg-primary-600 px-4 py-2 text-sm text-white">{{ $message->content }}</div>
                </div>

            @elseif ($message->role === 'assistant' && filled($message->tool_name))
                {{-- The call, and the result that answered it, as one line. The
                     result row itself is skipped below; pairing them here is
                     what turns two rows into one. Adjacent by construction: the
                     agent loop appends a call and its result per intent. --}}
                @php
                    $answer = $answers[$message->message_id] ?? null;
                    $shown = "opened[{$message->message_id}] ?? everything";
                @endphp

                <div>
                <div class="flex items-center gap-2 ps-1 text-xs text-gray-500 dark:text-gray-400">
                    <x-filament::icon
                        icon="heroicon-m-wrench-screwdriver"
                        class="h-4 w-4 shrink-0 text-gray-400 dark:text-gray-500"
                    />

                    <span class="font-mono">{{ $message->tool_name }}</span>

                    @if (isset($answer['error']))
                        <x-filament::badge color="danger" size="xs">
                            {{ __('portal.conversation.tool_failed') }}
                        </x-filament::badge>
                    @elseif (array_key_exists('records', $answer ?? []))
                        <x-filament::badge
                            :color="count($answer['records']) ? 'gray' : 'warning'"
                            size="xs"
                        >
                            {{ trans_choice('portal.conversation.records', count($answer['records'])) }}
                        </x-filament::badge>

                        @if (($answer['index_status'] ?? 'ready') !== 'ready')
                            <x-filament::badge color="warning" size="xs">
                                {{ $answer['index_status'] }}
                            </x-filament::badge>
                        @endif

                        @if ($answer['truncated'] ?? false)
                            <x-filament::badge color="gray" size="xs">
                                {{ __('portal.conversation.truncated') }}
                            </x-filament::badge>
                        @endif
                    @endif

                    <button
                        type="button"
                        x-on:click="opened[{{ $message->message_id }}] = ! ({{ $shown }})"
                        x-text="({{ $shown }})
                            ? @js(__('portal.conversation.hide_json'))
                            : @js(__('portal.conversation.see_json'))"
                        class="ms-auto shrink-0 text-xs font-medium text-primary-600 hover:underline
                               dark:text-primary-400"
                    >{{ __('portal.conversation.see_json') }}</button>
                </div>

                {{-- `display: none` inline rather than `x-cloak`: nothing in
                     the bundled CSS defines that attribute, so without it every
                     block flashes open before Alpine boots. --}}
                <div x-show="{{ $shown }}" style="display: none" class="mt-2 space-y-3 ps-7">
                    @foreach ($blocks[$message->message_id] ?? [] as $half => $code)
                        <div>
                            <div class="mb-1 flex items-center gap-2">
                                <span class="text-xs font-medium text-gray-500 dark:text-gray-400">
                                    {{ __('portal.conversation.' . $half) }}
                                </span>

                                <button
                                    type="button"
                                    x-data="{ done: false }"
                                    x-on:click="window.navigator.clipboard.writeText(@js($code['raw']));
                                                done = true;
                                                setTimeout(() => done = false, 1500)"
                                    x-text="done
                                        ? @js(__('portal.conversation.copied'))
                                        : @js(__('portal.conversation.copy'))"
                                    class="text-xs font-medium text-primary-600 hover:underline
                                           dark:text-primary-400"
                                >{{ __('portal.conversation.copy') }}</button>
                            </div>

                            <div class="overflow-x-auto rounded-lg text-xs">{{ $code['html'] }}</div>
                        </div>
                    @endforeach
                </div>
                </div>

            @elseif ($message->role === 'tool')
                {{-- Drawn as part of its call, above. --}}

            @else
                {{-- The answer, and what the reader made of it.
                     The badge sits here rather than in a column of its own
                     because of what is directly above it: the tool result the
                     answer was built from. A thumbs-down over `"records": []`
                     is the whole finding -- she told a reader the library has
                     nothing and the reader disagreed -- and the two are only
                     one fact when they are next to each other. --}}
                <div class="flex items-end justify-start gap-2">
                    <div class="max-w-2xl whitespace-pre-wrap rounded-xl rounded-bl-sm
                                bg-gray-100 px-4 py-2 text-sm dark:bg-white/10">{{ $message->content }}</div>

                    @if ($message->rating)
                        <x-filament::badge
                            :color="$message->rating->useful ? 'success' : 'danger'"
                            size="xs"
                            class="shrink-0"
                        >
                            {{ $message->rating->useful
                                ? __('portal.conversation.rated_useful')
                                : __('portal.conversation.rated_unhelpful') }}
                        </x-filament::badge>
                    @endif
                </div>
            @endif
        @endforeach
    </div>
    </div>
</x-filament-panels::page>
