<?php

namespace App\Filament\Library\Pages;

use App\Channels\Origin;
use App\Models\ChannelBinding;
use App\Models\Tenant;
use App\Models\TenantWidget;
use BackedEnum;
use Closure;
use Filament\Actions\Action;
use Filament\Facades\Filament;
use Filament\Forms\Components\ColorPicker;
use Filament\Forms\Components\Select;
use Filament\Forms\Components\TagsInput;
use Filament\Forms\Components\Textarea;
use Filament\Forms\Components\TextInput;
use Filament\Forms\Components\Toggle;
use Filament\Forms\Concerns\InteractsWithForms;
use Filament\Forms\Contracts\HasForms;
use Filament\Notifications\Notification;
use Filament\Pages\Page;
use Filament\Schemas\Components\Section;
use Filament\Schemas\Components\Utilities\Get;
use Filament\Schemas\Components\View;
use Filament\Schemas\Schema;
use Filament\Support\Icons\Heroicon;

/**
 * How this library's MARC.IA introduces herself.
 *
 * A Page rather than a Resource: there is exactly one row per library, so a
 * Resource would render a list of one with a "New" button that must never be
 * pressed.
 *
 * Nothing here reads or writes a tenant_id. The row is found by the row-level
 * security policy: inside this request's scope, `TenantWidget::first()` can
 * only ever return this library's row, because no other row is visible. That
 * is the point of binding the request rather than filtering the query.
 */
class WidgetSettings extends Page implements HasForms
{
    use InteractsWithForms;

    protected string $view = 'filament.library.pages.widget-settings';

    protected static string|BackedEnum|null $navigationIcon = Heroicon::OutlinedChatBubbleLeftRight;

    /** @var array<string, mixed>|null */
    public ?array $data = [];

    public static function getNavigationLabel(): string
    {
        return __('portal.widget.nav');
    }

    public function getTitle(): string
    {
        return __('portal.widget.title');
    }

    public function mount(): void
    {
        $record = $this->record();
        $state = $record->attributesToArray();

        // A blank greeting means the widget opens with no greeting at all, so
        // the field starts with something worth keeping rather than empty. It
        // is a suggestion and not a default: it is filled into the form, the
        // librarian edits or clears it, and what she saves is what a reader
        // sees. Nothing regenerates it later, because a greeting she wrote is
        // hers.
        if (blank($state['welcome_message'] ?? null)) {
            $state['welcome_message'] = $this->suggestedWelcome($record->assistant_name);
        }

        /*
         * The web channel's half of this page. Two tables behind one form: the
         * allowlist and the switch belong to channel_bindings, and they are
         * here because this is where the widget is published -- the address
         * goes in, the line comes out, the key turns, in one place.
         *
         * Both are found by the row-level security policy, like everything else
         * on this screen. A library whose web channel was deleted gets neither
         * field, and the view below says so.
         */
        $channel = $this->webChannel();
        $state['allowed_origins'] = $channel?->allowed_origins ?? [];
        $state['channel_enabled'] = (bool) $channel?->enabled;

        $this->form->fill($state);
    }

    /**
     * A greeting worth starting from, in this library's own name.
     *
     * Generated rather than left to be typed, because the name a library goes
     * by is already in the registry and typing it again is how a widget ends
     * up welcoming readers to "biblioteca demofranca" when the library is
     * called NARC.
     */
    private function suggestedWelcome(?string $assistantName): string
    {
        $tenant = Tenant::query()->find(Filament::auth()->user()->tenant_id);

        return __('portal.widget.welcome_suggestion', [
            'library' => $tenant?->display_name ?: __('portal.widget.your_library'),
            'assistant' => $assistantName ?: 'MARC.IA',
        ]);
    }

    public function form(Schema $schema): Schema
    {
        return $schema
            ->components([
                Section::make(__('portal.widget.section.persona'))
                    ->description(__('portal.widget.section.persona_help'))
                    ->schema([
                        // `live` on every field below, because the preview in
                        // the section after this one draws from this state. On
                        // blur for the text ones: a redraw per keystroke is a
                        // round trip per keystroke.
                        TextInput::make('assistant_name')
                            ->label(__('portal.widget.assistant_name'))
                            // Said here because the name now leaves this
                            // screen in two directions: over the socket, which
                            // is live, and inside the installation line, which
                            // is a copy somebody pasted and will not update
                            // itself.
                            ->helperText(__('portal.widget.assistant_name_help'))
                            ->required()
                            ->live(onBlur: true)
                            ->maxLength(60),

                        Select::make('persona_tone')
                            ->label(__('portal.widget.persona_tone'))
                            ->options([
                                'formal' => __('portal.widget.tone.formal'),
                                'neutral' => __('portal.widget.tone.neutral'),
                                'friendly' => __('portal.widget.tone.friendly'),
                            ])
                            ->required(),

                        Textarea::make('welcome_message')
                            ->label(__('portal.widget.welcome_message'))
                            ->helperText(__('portal.widget.welcome_message_help'))
                            ->rows(3)
                            ->live(onBlur: true)
                            ->maxLength(500),

                        Select::make('default_locale')
                            ->label(__('portal.widget.default_locale'))
                            ->options(['pt-BR' => 'Português (Brasil)', 'en' => 'English'])
                            ->live()
                            ->required(),
                    ]),

                Section::make(__('portal.widget.section.appearance'))
                    ->schema([
                        ColorPicker::make('theme_color')
                            ->label(__('portal.widget.theme_color'))
                            ->live(onBlur: true)
                            ->required(),
                    ]),

                Section::make(__('portal.widget.section.install'))
                    ->description(__('portal.widget.section.install_help'))
                    ->schema([
                        /*
                         * Where the widget may be embedded, and whether it is
                         * on. Both belong to the web channel rather than to the
                         * widget row, and both are edited on the Channels
                         * screen too -- the same column of the same row, so the
                         * two screens cannot disagree.
                         *
                         * They are here because this is where she is standing
                         * when she has the OPAC page in front of her: the
                         * address goes in, the line comes out, the key turns.
                         * It used to be an operator typing the address on the
                         * registration form, which was a guess that nothing
                         * could correct afterwards.
                         */
                        TagsInput::make('allowed_origins')
                            ->label(__('portal.channel.allowed_origins'))
                            ->helperText(__('portal.channel.allowed_origins_help'))
                            ->placeholder('https://opac.example.org')
                            ->visible(fn (): bool => $this->webChannel() !== null)
                            // Normalized on the way in, exactly as the Channels
                            // screen does it, so the allowlist holds one row per
                            // origin rather than four spellings of the same one.
                            ->dehydrateStateUsing(fn (?array $state): array => array_values(array_unique(
                                array_filter(array_map(Origin::normalize(...), $state ?? []))
                            ))),

                        View::make('filament.library.widget-embed'),

                        Toggle::make('channel_enabled')
                            ->label(__('portal.channel.enabled'))
                            ->helperText(__('portal.widget.channel_enabled_help'))
                            ->visible(fn (): bool => $this->webChannel() !== null)
                            /*
                             * The database refuses an enabled web channel with
                             * an empty allowlist -- channel_bindings_web_needs_
                             * an_origin -- and that constraint is the real
                             * guarantee. This rule only makes the screen arrive
                             * first: without it the refusal surfaces as a
                             * Postgres exception on save, which is a 500 where
                             * a field error belongs.
                             */
                            ->rule(static fn (Get $get): Closure => static function (
                                string $attribute,
                                mixed $value,
                                Closure $fail,
                            ) use ($get): void {
                                if (! $value) {
                                    return;
                                }

                                $origins = array_filter(array_map(
                                    Origin::normalize(...),
                                    $get('allowed_origins') ?? [],
                                ));

                                if ($origins === []) {
                                    $fail(__('portal.channel.enable_needs_origin'));
                                }
                            }),
                    ]),
            ])
            ->statePath('data');
    }

    public function save(): void
    {
        $state = $this->form->getState();

        // Split before the update. These two are the channel's, and handing
        // TenantWidget keys it has no column for is how a save starts failing
        // for a reason nothing on screen explains.
        $origins = $state['allowed_origins'] ?? [];
        $enabled = (bool) ($state['channel_enabled'] ?? false);
        unset($state['allowed_origins'], $state['channel_enabled']);

        // No tenant_id in either payload, and none accepted: both rows are the
        // only ones this request can see.
        $this->record()->update($state);
        $this->webChannel()?->update([
            'allowed_origins' => $origins,
            'enabled' => $enabled,
        ]);

        Notification::make()->success()->title(__('portal.widget.saved'))->send();
    }

    /**
     * @return array<Action>
     */
    protected function getFormActions(): array
    {
        return [
            Action::make('save')->label(__('portal.save'))->submit('save'),
        ];
    }

    /**
     * This library's web channel, or null when it has none yet.
     *
     * Found by the row-level security policy rather than by a where clause:
     * inside this request's scope there is no other library's binding to
     * return. Same reason the widget row is fetched without a tenant_id.
     */
    public function webChannel(): ?ChannelBinding
    {
        return ChannelBinding::query()->where('channel', 'web')->first();
    }

    /**
     * The one line a library pastes into its OPAC.
     *
     * Generated rather than transcribed. A librarian copying a key by hand can
     * copy the wrong one, and a widget bound to another library is a failure
     * that looks like a working install.
     *
     * `data-name` and `data-color` are here for one reason: the launcher
     * button is on screen before any socket opens, and the socket only opens
     * when somebody clicks the button — so those two are the only parts of the
     * widget that cannot wait for the library's configuration to arrive, and
     * the button cannot be hidden until it either. Everything else comes with
     * the session.
     *
     * They are therefore allowed to go stale: changing either in the form
     * above fixes the button from the first conversation onward, and fixes
     * this line for whoever pastes it next.
     */
    public function embed(): ?string
    {
        $channel = $this->webChannel();

        if ($channel === null) {
            return null;
        }

        return sprintf(
            '<script src="%s/widget.js" data-key="%s" data-name="%s" data-color="%s"></script>',
            $this->widgetUrl(),
            e($channel->binding_key),
            e($this->fromForm('assistant_name', 'MARC.IA')),
            e($this->fromForm('theme_color', '#4f46e5')),
        );
    }

    /**
     * One field as the form has it, unsaved edits included, or the default.
     *
     * The defaults match widget.js's own, which is the point: a snippet and a
     * widget that disagree about the fallback produce a button one colour and
     * a panel another, and nothing anywhere is wrong enough to notice.
     */
    private function fromForm(string $field, string $fallback): string
    {
        $typed = $this->data[$field] ?? null;

        return filled($typed) ? $typed : $fallback;
    }

    /**
     * Where a reader's browser reaches /libvox-core.
     *
     * Guessed from the request the librarian is making, because the alternative
     * was guessing `http://127.0.0.1:8092` -- right on a laptop, wrong on every
     * server, and wrong in the way that costs the most: the snippet copies
     * cleanly, goes into an OPAC, and loads nothing, with no error on the page
     * to say why. A librarian reaching this panel at some address is evidence
     * about an address that resolves.
     *
     * **The port is only appended when the panel is being reached on one.**
     * Direct, Core is one port away from the panel on the same host, and the
     * guess holds. Behind a proxy it does not, and provably: `deploy/`'s own
     * `host.env.example` tells the operator to bind 8090 and 8092 to loopback
     * there, because Caddy reaches the containers by name -- so a panel served
     * on 443 that emits `https://host:8092/widget.js` is naming a port its own
     * deployment guide just closed. Same origin is the better guess there,
     * being what a single-domain proxy serves and what a librarian is looking
     * at while she copies the line.
     *
     * Neither guess can be right for the two-domain deployment in
     * `deploy/proxy/Caddyfile`, where the widget has a hostname of its own.
     * That is what LIBVOX_WIDGET_URL is for, and it wins over both.
     */
    public function widgetUrl(): string
    {
        $configured = config('libvox.widget_url');

        if (filled($configured)) {
            return rtrim((string) $configured, '/');
        }

        $scheme = request()->getScheme();
        $origin = $scheme.'://'.request()->getHost();
        $port = (int) request()->getPort();

        // getPort() answers 443 and 80 for a request that carried no port,
        // which is the same thing as saying somebody else is listening on the
        // outside. Symfony reads the forwarded headers for this, so it is the
        // reader's port and not the container's.
        if ($port === 443 || $port === 80) {
            return $origin;
        }

        return $origin.':'.config('libvox.widget_port');
    }

    private function record(): TenantWidget
    {
        $tenantId = Filament::auth()->user()->tenant_id;

        // firstOrCreate rather than a provisioning step that could be skipped:
        // a library with no widget row would render an empty form that saves
        // nothing, which reads as a bug rather than as missing setup.
        return TenantWidget::firstOrCreate(['tenant_id' => $tenantId]);
    }
}
