<x-filament-panels::page>
    <form wire:submit="save">
        {{ $this->form }}

        <div class="mt-6 flex gap-3">
            @foreach ($this->getFormActions() as $action)
                {{ $action }}
            @endforeach
        </div>
    </form>

    {{--
        The result of the last connection test.

        Its detail is shown in full: it names this library's own server and the
        status that server returned, and the librarian is the only person who
        can act on either. The rule that collapses this kind of detail protects
        a patron reading a chat window.
    --}}
    <x-filament::modal id="ils-check" width="lg">
        <x-slot name="heading">
            {{ __('portal.ils.check.heading') }}
        </x-slot>

        @if ($this->check !== null)
            <div class="space-y-3">
                <div @class([
                    'text-sm font-medium',
                    'text-success-600 dark:text-success-400' => $this->check['ok'] && $this->check['records_seen'] > 0,
                    'text-warning-600 dark:text-warning-400' => $this->check['ok'] && $this->check['records_seen'] === 0,
                    'text-danger-600 dark:text-danger-400' => ! $this->check['ok'],
                ])>
                    {{ $this->checkMessage() }}
                </div>

                @if (filled($this->check['detail']))
                    <pre class="overflow-x-auto rounded-lg bg-gray-950/5 p-3 text-xs
                                whitespace-pre-wrap dark:bg-white/5">{{ $this->check['detail'] }}</pre>
                @endif
            </div>
        @endif
    </x-filament::modal>
</x-filament-panels::page>
