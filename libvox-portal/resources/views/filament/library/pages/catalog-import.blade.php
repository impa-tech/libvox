{{--
    An import while it is happening.

    The count is the fact and is always shown; the bar is an estimate against
    the previous import and appears only when there was one. See CatalogImport
    for why there is no total to ask Koha for.
--}}
<x-filament-panels::page>
    @php
        $progress = $this->progress();
        $percentage = $this->percentage();
    @endphp

    {{--
        Two rules, and only two.

        The rest of this page is utility classes now. It was fifty-eight lines
        of `libvox-` CSS reading Filament's custom properties, because the
        project had no Tailwind build and every utility was inert -- the first
        version of this bar was written with `h-2` and `bg-primary-600` and
        rendered as a div with no height and no colour. The build exists, so
        that workaround is gone.

        What is left is not a workaround. A `@keyframes` sweep and a striped
        gradient are not utility classes in any framework, and the alternative
        -- an `@utility` and an `--animate-` entry in the stylesheet both panels
        share -- would put one screen's progress bar in everyone's CSS.
    --}}
    <style>
        /* Past the estimate the catalogue has grown, and the bar has run out of
           room to say so. The stripes say "more than expected" where a full bar
           alone would look merely finished. */
        .libvox-fill--over {
            background: repeating-linear-gradient(
                45deg,
                var(--primary-600) 0 .6rem,
                var(--primary-400) .6rem 1.2rem
            );
        }

        /* No previous import to measure against, so there is no proportion to
           draw. This says "working" without claiming to know how far along. */
        .libvox-fill--unknown { animation: libvox-sweep 1.6s ease-in-out infinite; }
        @keyframes libvox-sweep {
            0%   { margin-left: -35%; }
            100% { margin-left: 100%; }
        }
        @media (prefers-reduced-motion: reduce) {
            .libvox-fill--unknown { animation: none; opacity: .4; }
        }
    </style>

    {{-- Polling stops once there is nothing left to watch: a tab left open
         overnight should not redraw the same number 3,600 times an hour. --}}
    <div @if ($this->watching()) wire:poll.1s @endif class="flex flex-col gap-3">

        @if ($progress['state'] === 'importing')
            <div class="flex items-baseline gap-2">
                <span class="text-4xl font-semibold leading-none tabular-nums">
                    @if ($percentage !== null)
                        {{ $percentage }}%
                    @else
                        {{ number_format($progress['imported'], 0, ',', '.') }}
                    @endif
                </span>
                <span class="text-sm text-gray-500 dark:text-gray-400">
                    @if ($percentage !== null)
                        {{ __('portal.import.records_so_far_short', [
                            'count' => number_format($progress['imported'], 0, ',', '.'),
                        ]) }}
                    @else
                        {{ __('portal.import.records_so_far') }}
                    @endif
                </span>
            </div>

            <div class="h-3 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
                @if ($percentage !== null)
                    {{-- The width is capped because a bar cannot be wider than
                         its track; the number above is not, and says 150% when
                         that is what happened. --}}
                    <div class="h-full rounded-full bg-primary-600 transition-[width] duration-1000 ease-linear motion-reduce:transition-none @if ($percentage > 100) libvox-fill--over @endif"
                         style="width: {{ min($percentage, 100) }}%"
                         role="progressbar"
                         aria-valuenow="{{ $percentage }}"
                         aria-valuemin="0"
                         aria-valuemax="100"></div>
                @else
                    <div class="h-full rounded-full bg-primary-600 transition-[width] duration-1000 ease-linear motion-reduce:transition-none w-[35%] libvox-fill--unknown"></div>
                @endif
            </div>

            <div class="text-sm text-gray-500 dark:text-gray-400">
                @if ($percentage !== null)
                    {{ __('portal.import.of_about', [
                        'percent' => $percentage,
                        'total' => number_format($progress['previous'], 0, ',', '.'),
                    ]) }}
                @else
                    {{ __('portal.import.no_estimate') }}
                @endif
            </div>

        @elseif ($progress['state'] === 'queued')
            <div class="flex items-center gap-3 text-sm text-gray-500 dark:text-gray-400">
                <x-filament::loading-indicator class="h-5 w-5" />
                {{ __('portal.import.queued') }}
            </div>

        @elseif ($progress['state'] === 'unclaimed')
            {{-- It does not claim the service is down: from here that cannot be
                 told apart from the service being busy with another library,
                 because row-level security means this page sees one library's
                 index and no other's. --}}
            <div class="text-sm font-medium text-warning-600 dark:text-warning-400">{{ __('portal.import.unclaimed') }}</div>
            <div class="text-sm text-gray-500 dark:text-gray-400">{{ __('portal.import.unclaimed_help') }}</div>
            <pre class="w-fit max-w-full overflow-x-auto whitespace-pre-wrap rounded-lg bg-gray-100 p-3 text-xs dark:bg-gray-800">cd libvox-sync &amp;&amp; ./worker.sh</pre>

        @elseif ($progress['state'] === 'failed')
            <div class="text-sm font-medium text-danger-600 dark:text-danger-400">{{ __('portal.import.failed') }}</div>
            @if (filled($progress['error']))
                {{-- Verbatim. It names the library's own server and what it
                     answered, and she is the only person who can act on
                     either. --}}
                <pre class="w-fit max-w-full overflow-x-auto whitespace-pre-wrap rounded-lg bg-gray-100 p-3 text-xs dark:bg-gray-800">{{ $progress['error'] }}</pre>
            @endif

        @else
            <div class="flex items-baseline gap-2">
                <span class="text-4xl font-semibold leading-none tabular-nums">
                    {{ number_format($progress['previous'], 0, ',', '.') }}
                </span>
                <span class="text-sm text-gray-500 dark:text-gray-400">{{ __('portal.import.records_indexed') }}</span>
            </div>
            <div class="h-3 w-full overflow-hidden rounded-full bg-gray-200 dark:bg-gray-700">
                <div class="h-full rounded-full bg-primary-600 transition-[width] duration-1000 ease-linear motion-reduce:transition-none" style="width: 100%"></div>
            </div>
            <div class="text-sm font-medium text-success-600 dark:text-success-400">{{ __('portal.import.done') }}</div>
        @endif
    </div>
</x-filament-panels::page>
